# Phase C — rewire the Pi `blazend-player` onto `domains/blazend-audiobook`

> **HANDOFF for the `paul` / `jessica` session (Linux, can build ALSA).**
> The `rachel` (macOS) session finished Phases A + B on branch
> `refactor/domain-architecture`: the shared `domains/blazend-audiobook` Rust
> engine (decode + resume seek + dynamics + position, behind an `AudioSink`
> trait) exists, is tested, and drives the Mac player over a cpal sink. **This
> step is Pi-only and cannot be compiled or verified on macOS (ALSA).** It's
> left for you to execute and verify with `make test-fast` on Linux.

## Why

The audiobook-playback engine now lives once, under `domains/blazend-audiobook`
(domains for common code). The Pi's `rpi5/voice-output/blazend-player` currently
still carries **its own copy** of that logic (decode/seek/dynamics/position) — a
temporary duplication. Phase C removes the duplication: `blazend-player` links
the shared engine + an **ALSA `AudioSink`**, keeping its radio/HLS streaming path
and CLI exactly as they are.

## Scope / invariants

- **CLI unchanged.** Every existing flag (`--device`, `--start-seconds`,
  `--position-file`, `--gain`, `--no-level`, `--compress`, `--target-db`,
  `--max-boost-db`, `--limit-db`, `--comp-*`, `--prebuffer-ms`, `--alsa-buffer-ms`,
  `--loop`) keeps working. The orchestrator (`supervisor.py`) calls it the same way.
- **Radio/HLS streaming stays in `blazend-player`.** The shared engine only owns
  **local-file** playback (that's all rachel needs and all the audiobook path
  uses). For a stream source, keep the existing `open_media`/`open_http_icy`/HLS
  path and the existing `run_output`. Only the **file** path routes through the
  shared engine. (Alternatively, later: lift the streaming readers into the shared
  crate too — not required now.)
- **`make test-fast` stays green** (the gate) — clippy `-D warnings` + rustfmt on
  both Rust workspaces, plus the Python tiers.
- Same `{"seconds":X,"done":bool}` position-file format (the shared engine's
  `write_position` already matches byte-for-byte).

## Steps

1. **Add the dep.** In `rpi5/crates/Cargo.toml` `[workspace.dependencies]` add
   `blazend-audiobook = { path = "../../domains/blazend-audiobook" }` and in
   `rpi5/voice-output/blazend-player/Cargo.toml` `[dependencies]` add
   `blazend-audiobook = { workspace = true }` (NO `cpal-sink` feature — the Pi
   uses ALSA). Confirm the domains crate cross-compiles for aarch64 (it's pure
   symphonia; it should).

2. **Add an ALSA sink** implementing `blazend_audiobook::AudioSink` +
   `SinkFactory`. Lift the ALSA setup + `io.writei` + `try_recover` from the
   current `run_output`. Ready-to-use starting point:

   ```rust
   // rpi5/voice-output/blazend-player/src/alsa_sink.rs
   use alsa::pcm::{Access, Format, HwParams, PCM};
   use alsa::{Direction, ValueOr};
   use anyhow::{Context, Result};
   use blazend_audiobook::{AudioSink, SinkFactory};

   pub struct AlsaSinkFactory { pub device: String, pub buffer_ms: u32 }

   impl SinkFactory for AlsaSinkFactory {
       fn open(&self, rate: u32, channels: usize) -> Result<Box<dyn AudioSink>> {
           let pcm = PCM::new(&self.device, Direction::Playback, false)
               .with_context(|| format!("open ALSA device {}", self.device))?;
           {
               let hwp = HwParams::any(&pcm)?;
               hwp.set_channels(channels as u32)?;
               hwp.set_rate(rate, ValueOr::Nearest)?;
               hwp.set_format(Format::s16())?;
               hwp.set_access(Access::RWInterleaved)?;
               let buf = (i64::from(rate) * i64::from(self.buffer_ms) / 1000).max(2048);
               hwp.set_buffer_size_near(buf)?;
               hwp.set_period_size_near((buf / 4).max(256), ValueOr::Nearest)?;
               pcm.hw_params(&hwp)?;
           }
           Ok(Box::new(AlsaSink { pcm }))
       }
   }

   pub struct AlsaSink { pcm: PCM }

   impl AudioSink for AlsaSink {
       fn write(&mut self, interleaved: &[i16]) -> Result<()> {
           let io = self.pcm.io_i16()?;
           if let Err(e) = io.writei(interleaved) {
               self.pcm.try_recover(e, true).ok();
               let _ = io.writei(interleaved);
           }
           Ok(())
       }
       fn drain(&mut self) { let _ = self.pcm.drain(); }
   }
   ```

   Note: the shared engine's consumer loop already does the prebuffer + jitter
   buffer + dynamics + position writes and hands the sink `~100 ms` period chunks,
   so the sink only needs the ALSA open + paced `writei`. (Re-open `io_i16()` per
   write is fine, or cache it — match current behavior.)

3. **Route the file path through the shared engine.** In `play_once`, when the
   source is a **local file** (`!is_url`), replace the decode/seek/decode_loop/
   `run_output` block with:

   ```rust
   use blazend_audiobook::{play_file, DynamicsConfig, FileConfig};
   let cfg = FileConfig {
       source: args.source.clone().into(),
       start_seconds: args.start_seconds,
       position_file: args.position_file.clone().map(Into::into),
       prebuffer_ms: args.prebuffer_ms,
       dynamics: DynamicsConfig {
           pre_gain: args.gain, level: !args.no_level, target_db: args.target_db,
           max_boost_db: args.max_boost_db, compress: args.compress,
           comp_threshold_db: args.comp_threshold_db, comp_ratio: args.comp_ratio,
           comp_makeup_db: args.comp_makeup_db, limit_db: args.limit_db,
       },
   };
   play_file(&AlsaSinkFactory { device: args.device.clone(), buffer_ms: args.alsa_buffer_ms }, &cfg)?;
   ```

   Keep the **URL/stream** branch on the existing `run_output` (radio is
   unaffected). Then **delete** the now-duplicated file-only helpers from
   `main.rs` that moved to the crate — `make_decoder`, `prime_decode`,
   `decode_loop`, `write_position`, and the `Dynamics`/`DynamicsCfg` structs —
   **only if** the streaming branch no longer needs them; if radio still uses
   `Dynamics`/`run_output`, keep those and delete only what's truly unused (let
   `cargo build` + clippy's dead-code lint guide you).

4. **Verify on Linux:**
   ```bash
   cd rpi5/crates && cargo build --release -p blazend-player
   cargo clippy -p blazend-player --all-targets -- -D warnings
   make test-fast                     # both Rust workspaces + Python tiers
   ```
   Then a real playback check on the Pi (or paul with a speaker): play a Wolne
   Lektury chapter with `--position-file`, confirm audio + the position file
   updates + resume via `--start-seconds` still works, and that **radio still
   plays** (the streaming path is untouched).

5. **Commit** on the shared branch, e.g.
   `refactor(rpi5): link blazend-player to domains/blazend-audiobook (file path + ALSA sink)`
   and note it so the rachel session knows the duplication is gone.

## Coordination notes for the rachel session

- The shared engine's public API (as of Phase B): `play_file(&dyn SinkFactory,
  &FileConfig)`, `FileConfig`, `DynamicsConfig` (dB-valued, `.defaults()` matches
  the `blazend-player` CLI defaults), `AudioSink` (`write` + `drain`),
  `SinkFactory`, `write_position`, plus `CpalSink`/`CpalSinkFactory` under the
  `cpal-sink` feature. If Phase C needs an API tweak (e.g. exposing the streaming
  path), change it in `domains/blazend-audiobook` and ping — rachel's player
  depends on the same crate.
- Until Phase C lands, `blazend-player` and `domains/blazend-audiobook` **both**
  contain the file-playback engine. That's an intentional, temporary duplication;
  the Pi runtime is unaffected (it still runs its own copy).
