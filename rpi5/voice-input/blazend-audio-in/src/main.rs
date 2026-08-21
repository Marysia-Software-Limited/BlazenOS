//! `blazend-audio-in` — microphone capture, shared-memory ring buffer, VAD feed.
//!
//! Real mode (M2): opens the ReSpeaker/WM8960 HAT capture PCM via the **`alsa`
//! crate with RW (`readi`) access** — deliberately *not* cpal/mmap, because
//! cpal locks both PCM substreams of the i2s card and blocks playback on the
//! same HAT (the TTS speaker). RW capture leaves the playback substream free,
//! so the HAT runs **full-duplex** (mic in + Jessica's voice out on one card),
//! exactly like `arecord` + `aplay`. We open `plughw:…` so the codec's native
//! rate/channels are converted to **mono-ish 16 kHz** (we capture stereo and
//! downmix), write PCM into the shared ring (`runtime_dir()/audio-ring.shm`)
//! that the Python ASR reads, and run an energy VAD (`vad.rs`) publishing
//! `vad.start` / `vad.end` on `audio-in.sock`. A periodic `system.event`
//! heartbeat keeps `blazend-health` from declaring mic starvation. With no
//! device (WSL/CI, or no HAT) it emits `mic.absent` and falls back to
//! synthetic frames so the rest of the stack still has a heartbeat.

mod vad;

use std::thread;
use std::time::Duration;

use anyhow::{anyhow, Context, Result};
use blazend_audioring::RingWriter;
use blazend_ipc::{runtime_dir, Event, EventEnvelope, Publisher};
use clap::Parser;
use vad::{EnergyVad, VadEvent};

/// ASR-facing capture rate; the ring always stores mono 16 kHz i16.
const TARGET_RATE: u32 = 16_000;
/// Capture mono and let `plughw` do any channel conversion — works for both the
/// stereo WM8960 HAT (downmixes its two mics) and a mono USB mic (passes
/// through). Opening a mono device as stereo scrambles the samples, so mono is
/// the portable choice.
const CAPTURE_CHANNELS: u32 = 1;

#[derive(Parser, Debug)]
#[command(name = "blazend-audio-in", version)]
struct Args {
    /// Emit synthetic frames instead of touching real hardware.
    #[arg(long)]
    mock: bool,
    /// ALSA capture PCM. A `plughw:…` name is RW-shareable, so the single Jabra
    /// SPEAK 410 USB stays full-duplex (TTS can play on the same card while we
    /// capture). A bare token is wrapped as `plughw:CARD=<token>,DEV=0`.
    #[arg(long, default_value = "plughw:CARD=USB,DEV=0")]
    device: String,
    /// Ring buffer length in seconds (mirrors `audio.yaml input.ring_buffer_seconds`).
    #[arg(long, default_value_t = 3)]
    ring_seconds: u32,
    /// VAD frame size in milliseconds (`audio.yaml input.frame_ms`).
    #[arg(long, default_value_t = 20)]
    frame_ms: u32,
    /// VAD open threshold (linear i16 RMS) — speech starts above this.
    #[arg(long, default_value_t = 1800.0)]
    open_rms: f32,
    /// VAD close threshold (linear i16 RMS) — silence below this.
    #[arg(long, default_value_t = 1100.0)]
    close_rms: f32,
    /// VAD open multiplier over the learned ambient noise floor.
    #[arg(long, default_value_t = 2.5)]
    open_mult: f32,
    /// VAD close multiplier over the learned ambient noise floor.
    #[arg(long, default_value_t = 1.6)]
    close_mult: f32,
    /// Trailing silence before an utterance ends (ms).
    #[arg(long, default_value_t = 300)]
    hangover_ms: u32,
    /// Minimum speech before an utterance opens (ms).
    #[arg(long, default_value_t = 150)]
    min_speech_ms: u32,
    /// Maximum length of a single utterance (ms). The VAD force-closes at this
    /// cap even if the energy never drops below `close_rms` — a safety net for a
    /// noisy capture floor that would otherwise keep an utterance open forever
    /// and starve ASR of segments.
    #[arg(long, default_value_t = 8000)]
    max_speech_ms: u32,
    /// High-pass cutoff (Hz) applied to capture before the ring + VAD, to reject
    /// low-frequency rumble and especially **mains hum** (50/60 Hz ground-loop),
    /// which otherwise dominates the RMS and masks speech. `0` disables it.
    /// Two cascaded one-pole stages (~12 dB/oct); speech formants pass intact.
    #[arg(long, default_value_t = 180.0)]
    hp_cutoff: f32,
}

/// One-pole high-pass: `y[n] = a·(y[n-1] + x[n] − x[n-1])`, `a = fs/(2π·fc+fs)`.
/// State persists across capture chunks. Two in series give a 2nd-order roll-off
/// — enough to push a 50 Hz hum ~20 dB below 300 Hz+ speech.
struct HighPass {
    a: f32,
    px: f32,
    py: f32,
}

impl HighPass {
    fn new(fc: f32, fs: f32) -> Self {
        let a = fs / (2.0 * std::f32::consts::PI * fc + fs);
        Self {
            a,
            px: 0.0,
            py: 0.0,
        }
    }
    #[inline]
    fn step(&mut self, x: f32) -> f32 {
        let y = self.a * (self.py + x - self.px);
        self.px = x;
        self.py = y;
        y
    }
}

fn ring_path() -> std::path::PathBuf {
    runtime_dir().join("audio-ring.shm")
}

/// Resolve a `--device` value to an ALSA PCM name. Full names (`…:…`) and
/// `default` pass through; a bare token becomes a shareable `plughw` PCM.
fn alsa_device(d: &str) -> String {
    let d = d.trim();
    if d.is_empty() || d == "default" || d.contains(':') {
        if d.is_empty() {
            "default".to_string()
        } else {
            d.to_string()
        }
    } else {
        format!("plughw:CARD={d},DEV=0")
    }
}

fn f32_to_i16(x: f32) -> i16 {
    (x.clamp(-1.0, 1.0) * 32767.0) as i16
}

/// ms of audio captured so far — a monotonic, clock-free `ts_ms`.
fn ts_from_pos(write_pos: u64) -> u64 {
    write_pos * 1000 / TARGET_RATE as u64
}

/// Open the capture PCM with **RW interleaved** access (not mmap) so the card's
/// playback substream stays free for TTS. Requests mono-downmix-friendly
/// stereo at the target rate; `plughw` converts from the codec's native clock.
#[allow(dead_code)] // kept for reference; capture now goes via the arecord subprocess
fn open_capture(device: &str, channels: u32) -> Result<(alsa::pcm::PCM, u32)> {
    use alsa::pcm::{Access, Format, HwParams, PCM};
    use alsa::{Direction, ValueOr};
    let pcm = PCM::new(device, Direction::Capture, false)
        .with_context(|| format!("open ALSA capture {device}"))?;
    {
        let hwp = HwParams::any(&pcm)?;
        hwp.set_channels(channels)?;
        // Don't force the rate: a raw `hw:` device (no plug resampler) may only
        // support its native rate (e.g. a 48 kHz USB headset mic). Accept what
        // it offers and decimate to TARGET_RATE in the read loop.
        let _ = ValueOr::Nearest;
        hwp.set_format(Format::s16())?;
        hwp.set_access(Access::RWInterleaved)?;
        // Let ALSA/`plug` negotiate the buffer + period. Forcing small explicit
        // sizes here fought the plug resampler on a 48 kHz-native USB mic (it
        // needs ~3× slave buffering for 48 k→16 k) and produced attenuated,
        // near-silent reads. `arecord` works precisely because it doesn't force
        // these — so neither do we.
        pcm.hw_params(&hwp)?;
        let neg_rate = hwp.get_rate().unwrap_or(0);
        let neg_ch = hwp.get_channels().unwrap_or(0);
        let neg_buf: i64 = hwp.get_buffer_size().unwrap_or(-1);
        let neg_per: i64 = hwp.get_period_size().unwrap_or(-1);
        tracing::warn!(
            neg_rate,
            neg_ch,
            neg_buf,
            neg_per,
            "DIAG negotiated hwparams"
        );
    }
    let actual_rate = {
        let hwc = pcm.hw_params_current()?;
        hwc.get_rate().unwrap_or(TARGET_RATE)
    };
    // Force start-on-first-frame via swparams, then prepare + start explicitly.
    // Without this the capture stream can sit un-started and `readi` returns a
    // steady near-silent floor (arecord sets this; we didn't).
    {
        let swp = pcm.sw_params_current()?;
        swp.set_start_threshold(1)?;
        swp.set_avail_min(1)?;
        pcm.sw_params(&swp)?;
    }
    pcm.prepare()?;
    let _ = pcm.start();
    Ok((pcm, actual_rate))
}

async fn run_capture(publisher: &Publisher, args: &Args) -> Result<()> {
    let device = alsa_device(&args.device);
    let channels = CAPTURE_CHANNELS as usize;

    // Open the PCM inside the capture thread (PCM isn't Sync); report the open
    // result back so a failure falls through to the synthetic mock loop.
    let (ready_tx, ready_rx) = std::sync::mpsc::channel::<Result<(), String>>();
    let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel::<Vec<f32>>();
    let dev = device.clone();
    thread::Builder::new()
        .name("alsa-capture".into())
        .spawn(move || {
            use std::io::Read;
            use std::process::{Command, Stdio};
            // The hand-rolled alsa-crate `readi` path returned only a constant
            // near-silent floor on this hardware (every rate/buffer/start config
            // we tried), while `arecord` on the identical device captures
            // cleanly. So let `arecord` (libasound) own the capture + any plug
            // resampling, and read raw 16 kHz mono S16 from its stdout.
            let mut child = match Command::new("arecord")
                .args([
                    "-D", &dev, "-q", "-t", "raw", "-f", "S16_LE", "-r", "16000", "-c", "1",
                ])
                .stdout(Stdio::piped())
                .stderr(Stdio::null())
                .spawn()
            {
                Ok(c) => {
                    let _ = ready_tx.send(Ok(()));
                    c
                }
                Err(e) => {
                    let _ = ready_tx.send(Err(format!("arecord spawn failed: {e}")));
                    return;
                }
            };
            let mut out = match child.stdout.take() {
                Some(o) => o,
                None => return,
            };
            let mut bytes = [0u8; 3200]; // 1600 i16 frames = 100 ms @ 16 kHz mono
            loop {
                match out.read(&mut bytes) {
                    Ok(0) => break, // arecord exited
                    Ok(n) => {
                        let usable = n - (n % 2);
                        let mono: Vec<f32> = bytes[..usable]
                            .as_chunks::<2>()
                            .0
                            .iter()
                            .map(|b| f32::from(i16::from_le_bytes(*b)) / 32768.0)
                            .collect();
                        if tx.send(mono).is_err() {
                            break; // consumer gone
                        }
                    }
                    Err(_) => break,
                }
            }
            let _ = child.kill();
        })?;

    match ready_rx.recv() {
        Ok(Ok(())) => {}
        Ok(Err(e)) => return Err(anyhow!("ALSA capture open failed: {e}")),
        Err(_) => return Err(anyhow!("capture thread exited before opening")),
    }
    tracing::info!(%device, rate = TARGET_RATE, channels, "microphone capture starting (ALSA RW)");

    publisher
        .publish(EventEnvelope::new(
            "blazend-audio-in",
            0,
            Event::SystemEvent {
                kind: "mic.ready".into(),
                detail: Some(format!("{device} → 16kHz mono (RW, full-duplex)")),
            },
        ))
        .await?;

    let capacity = TARGET_RATE * args.ring_seconds;
    let mut ring = RingWriter::create(ring_path(), TARGET_RATE, 1, capacity)?;
    let mut vad = EnergyVad::new(
        args.open_rms,
        args.close_rms,
        args.open_mult,
        args.close_mult,
        args.hangover_ms,
        args.min_speech_ms,
        args.max_speech_ms,
        args.frame_ms,
    );
    let frame_size = (TARGET_RATE * args.frame_ms / 1000).max(1) as usize;
    tracing::info!(ring = ?ring_path(), "ring buffer live; listening");

    let mut frame_acc: Vec<i16> = Vec::with_capacity(frame_size);
    let mut last_heartbeat_pos: u64 = 0;
    // Most recent capture-chunk RMS, surfaced in the heartbeat log so we can see
    // what the VAD is actually hearing. Assigned at the top of every iteration
    // before the heartbeat reads it.
    let mut last_chunk_rms: f32;

    // Two cascaded one-pole high-pass stages reject the 50/60 Hz mains hum that
    // otherwise dominates the capture RMS (measured ~650 of hum masking speech).
    // Applied to the f32 stream before both the ring (so ASR sees clean audio)
    // and the VAD. `hp_cutoff == 0` disables filtering.
    let hp_on = args.hp_cutoff > 0.0;
    let mut hp1 = HighPass::new(args.hp_cutoff.max(1.0), TARGET_RATE as f32);
    let mut hp2 = HighPass::new(args.hp_cutoff.max(1.0), TARGET_RATE as f32);
    if hp_on {
        tracing::info!(cutoff_hz = args.hp_cutoff, "high-pass (anti-hum) enabled");
    }

    // Half-duplex marker set by blazend-audio-out while the speaker is playing.
    let speaker_marker = runtime_dir().join("speaker-busy");
    // Push-to-talk / wake activation marker: the HAT-button watcher (and, once a
    // working model exists, the wake-word detector) create this file to open one
    // listen window. Jessica is DEAF by default — she only listens after her name
    // or the button activates her. This is also what keeps ASR from ever being
    // flooded: under heavy ASR the WM8960 I2S floor spikes into the speech band,
    // so an always-listening VAD self-feeds a runaway loop. PTT removes that.
    let activate_marker = runtime_dir().join("activate");

    let mut listening = false; // deaf until activated by button/wake
                               // While listening, give the user a window to start speaking; if no speech
                               // arrives, go deaf again rather than sit open (and risk a noise trigger).
    let mut listen_deadline: Option<std::time::Instant> = None;
    const LISTEN_WINDOW: Duration = Duration::from_secs(8);

    // The capturer already delivers mono 16 kHz (plughw converted), so no
    // resample step is needed — high-pass, convert to i16, feed the ring + VAD.
    while let Some(mono) = rx.recv().await {
        let i16v: Vec<i16> = mono
            .iter()
            .map(|&x| {
                let s = if hp_on { hp2.step(hp1.step(x)) } else { x };
                f32_to_i16(s)
            })
            .collect();
        last_chunk_rms = vad::rms_i16(&i16v);
        ring.push(&i16v);

        // Activation: the button watcher / wake detector touches `activate` to
        // open a listen window. Consume it and start (or extend) listening.
        if activate_marker.exists() {
            let _ = std::fs::remove_file(&activate_marker);
            if !listening {
                tracing::info!("activated (button/wake) — listening");
            }
            listening = true;
            listen_deadline = Some(std::time::Instant::now() + LISTEN_WINDOW);
        }
        // Close the window if the user never started speaking in time.
        if let Some(dl) = listen_deadline {
            if std::time::Instant::now() >= dl {
                listening = false;
                listen_deadline = None;
            }
        }
        // Suppress while Jessica speaks (speaker-busy) OR while not activated. The
        // ring keeps filling for continuity.
        let marker = speaker_marker.exists();
        let suppressed = marker || !listening;
        if suppressed {
            frame_acc.clear();
        }
        for &sample in &i16v {
            if suppressed {
                break;
            }
            frame_acc.push(sample);
            if frame_acc.len() == frame_size {
                if let Some(event) = vad.push_frame(&frame_acc) {
                    let ts = ts_from_pos(ring.write_pos());
                    match event {
                        VadEvent::Start => {
                            tracing::info!("vad.start");
                            // Speech began within the window — cancel the no-speech
                            // timeout so a long utterance isn't cut off.
                            listen_deadline = None;
                            publisher
                                .publish(EventEnvelope::new(
                                    "blazend-audio-in",
                                    ts,
                                    Event::VadStart,
                                ))
                                .await?;
                        }
                        VadEvent::End { duration_ms } => {
                            tracing::info!(duration_ms, "vad.end");
                            publisher
                                .publish(EventEnvelope::new(
                                    "blazend-audio-in",
                                    ts,
                                    Event::VadEnd { duration_ms },
                                ))
                                .await?;
                            // One utterance per activation: go deaf until the next
                            // button press / wake. This also means the ASR + Bielik
                            // CPU burst that follows can't spike the capture floor
                            // and re-open the VAD (the runaway flood).
                            listening = false;
                            listen_deadline = None;
                            frame_acc.clear();
                            break;
                        }
                    }
                }
                frame_acc.clear();
            }
        }

        // ~1 s heartbeat so blazend-health doesn't flag mic starvation in silence.
        let pos = ring.write_pos();
        if pos - last_heartbeat_pos >= TARGET_RATE as u64 {
            last_heartbeat_pos = pos;
            tracing::info!(
                noise_floor = vad.noise_floor(),
                chunk_rms = last_chunk_rms,
                "heartbeat"
            );
            publisher
                .publish(EventEnvelope::new(
                    "blazend-audio-in",
                    ts_from_pos(pos),
                    Event::SystemEvent {
                        kind: "tick".into(),
                        detail: None,
                    },
                ))
                .await?;
        }
    }
    Ok(())
}

/// No real device: keep the ring + a heartbeat + a periodic synthetic utterance
/// so the downstream pipeline is exercisable headless (CI / WSL).
async fn mock_loop(publisher: &Publisher, args: &Args) -> Result<()> {
    let capacity = TARGET_RATE * args.ring_seconds;
    let mut ring = RingWriter::create(ring_path(), TARGET_RATE, 1, capacity)?;
    let mut ts: u64 = 0;
    loop {
        // Heartbeat every second for ~15 s, then a synthetic 1 s "utterance".
        for _ in 0..15 {
            tokio::time::sleep(Duration::from_secs(1)).await;
            ts += 1000;
            publisher
                .publish(EventEnvelope::new(
                    "blazend-audio-in",
                    ts,
                    Event::SystemEvent {
                        kind: "tick".into(),
                        detail: Some(format!("mock frame {ts}")),
                    },
                ))
                .await?;
        }
        let tone: Vec<i16> = (0..TARGET_RATE)
            .map(|i| {
                let t = i as f32 / TARGET_RATE as f32;
                ((t * 440.0 * std::f32::consts::TAU).sin() * 8000.0) as i16
            })
            .collect();
        ring.push(&tone);
        publisher
            .publish(EventEnvelope::new("blazend-audio-in", ts, Event::VadStart))
            .await?;
        tokio::time::sleep(Duration::from_millis(50)).await;
        publisher
            .publish(EventEnvelope::new(
                "blazend-audio-in",
                ts,
                Event::VadEnd { duration_ms: 1000 },
            ))
            .await?;
    }
}

#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()),
        )
        .init();

    let args = Args::parse();
    let publisher = Publisher::bind(runtime_dir().join("audio-in.sock")).await?;
    tracing::info!(socket = ?publisher.socket_path, "audio-in online");

    if args.mock {
        return mock_loop(&publisher, &args).await;
    }

    match run_capture(&publisher, &args).await {
        Ok(()) => Ok(()),
        Err(e) => {
            tracing::warn!(
                "no usable capture ({e}) (no mic/HAT attached, or WSL/CI) — falling back to synthetic frames"
            );
            publisher
                .publish(EventEnvelope::new(
                    "blazend-audio-in",
                    0,
                    Event::SystemEvent {
                        kind: "mic.absent".into(),
                        detail: Some("no usable ALSA capture device".into()),
                    },
                ))
                .await?;
            mock_loop(&publisher, &args).await
        }
    }
}
