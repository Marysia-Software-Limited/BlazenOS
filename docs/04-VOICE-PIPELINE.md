# 04 — Voice pipeline

End-to-end flow from microphone PCM to speaker PCM.

```
mic PCM ─▶ AEC/AGC ─▶ ring buffer ─▶ wake word ─▶ VAD ─▶ ASR ─▶ NLU ─▶ brain ─▶ TTS ─▶ speaker PCM
                          │             │           │       │      │      │       │
                          │             ▼           ▼       ▼      ▼      ▼       ▼
                          └────────── events on /run/blazen/* (Unix sockets, JSON)
```

## Stage 1 — Audio capture (`blazend-audio-in`, **Rust**)

- **Crate:** [`cpal`](https://crates.io/crates/cpal) over PipeWire/ALSA.
- **Sample rate:** 16 kHz mono int16 — the lingua franca for wake/VAD/ASR.
- **Frame size:** 20 ms (320 samples). Smaller frames lower wake-word
  latency but raise CPU; 20 ms is the sweet spot.
- **Ring buffer:** 3 s in shared memory (`/dev/shm/blazen-mic.ring`),
  written by Rust, read by Rust (`blazend-wake`) and Python
  (`blazend-asr`) via `memmap2` / `mmap`. Lock-free SPMC ring.
- **AEC/AGC:** WebRTC's `apm` linked as a thin Rust FFI shim
  (`rpi5/crates/blazend-audio-in/build.rs` builds the C++ AEC into a static
  library). Off by default in dev, on by default in release.

## Stage 2 — Wake word (`blazend-wake`, **Rust**)

- **Crate:** [`ort`](https://crates.io/crates/ort) (ONNX Runtime
  Rust bindings) running [openWakeWord](https://github.com/dscripka/openWakeWord)
  models.
- **Default wake words (bilingual):** `hey Jessica` (`jessica_en.onnx`)
  and `hej Jessico` (`jessica_pl.onnx`). Both models loop in parallel —
  whichever fires first wins and its `language` field hints ASR.
- **Fallback wake words:** `jarvis`, `alexa` (shipped pretrained).
- **Threshold:** 0.6 (probability) with a 200 ms cooldown.
- **Output:** `wake.detected` event with `{score, model, language, ts}`.

> **Why openWakeWord:** open, retrainable, CPU-friendly. Porcupine has
> better accuracy but its non-commercial tier is restrictive.

## Stage 3 — Voice Activity Detection (in `blazend-audio-in`, **Rust**)

- **Crate:** `ort` running [silero-vad](https://github.com/snakers4/silero-vad)
  ONNX, CPU.
- **Window:** 30 ms.
- **End-of-utterance:** 800 ms of silence after speech.
- **Output:** `vad.start`, `vad.end` events.

## Stage 4 — ASR (`blazend-asr`, **Python**)

- **Library:** [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
  via CTranslate2 INT8 (heavy work is C++; Python is the API shell).
- **Default model:** `small` (multilingual; PL + EN out of the box). The
  EN-only `small.en` is kept as an opt-in for the `lang_mode: en_only`
  build flavour. See [`13-LANGUAGES.md`](13-LANGUAGES.md).
- **Mode:** streaming — partial transcripts every 200 ms once VAD says
  speech started.
- **Output:** `asr.partial` (multiple), `asr.final` once VAD says end.
- **Language detection:** Whisper's built-in detector runs on the first
  1 s of audio. On low confidence (<0.7) the wake-word language hint
  wins; otherwise the detected language tags the rest of the pipeline.
- **Language switching:** auto by default (per utterance). Voice
  commands `"speak Polish"` / `"mów po angielsku"` pin a language until
  the user says `"detect my language"` / `"słuchaj uważnie"`.

## Stage 5 — NLU / intent routing (`blazend-nlu`, **Rust**)

We use a **hybrid** matcher:

1. **Regex/keyword router** first (fast path) — `blazend-nlu` subscribes to
   `asr.final`, runs the **shared `jessica-core` `IntentRouter`** over the
   bilingual `configs/intents/system.yaml`, and publishes `nlu.intent` on a
   match (`{intent, language, params, transcript}`). It matches a curated
   set of system commands ("volume up", "what time is it", "stop talking",
   "go to sleep") with EN+PL triggers. This is the **same Rust router** the
   iOS/Android apps use via `jessica-ffi` — one source of truth, no Python
   copy (see `docs/14-RUST-PYTHON-SPLIT.md`). Misses stay silent and fall
   through to the brain.
2. **LLM intent classification** as fallback — the LLM is asked to pick
   one of a known intent set OR `freeform` (chat).
3. **Tool calls** — when an intent maps to a tool (timer, weather, home
   automation), the LLM produces a JSON function call which the
   orchestrator dispatches.

Fast path keeps "stop talking" reliable even when the brain is busy.

## Stage 6 — Brain / LLM (`blazend-brain`, **Python**)

- **Engine selector:** chooses between CPU (`llama-cpp-python`) and
  optional Hailo (`HailoRT` Python) at startup. See
  [`12-ML-ACCELERATOR.md`](12-ML-ACCELERATOR.md). The heavy work is
  native C/C++ underneath; Python is the orchestration shell.
- **Default CPU engine:** `llama-cpp-python` (NEON automatic on Pi 5).
- **Default model:** `qwen2.5-3b-instruct-q4_k_m.gguf` (~2.0 GB).
- **Optional accelerator path:** when an AI HAT+ (Hailo) is detected,
  the same conversation contract is served by a Hailo `.hef` instead
  of the GGUF, with TTFT ≈ 120 ms (Hailo-10H) and 35 tok/s vs. CPU's
  350 ms and 12 tok/s. On any HailoRT error, the next utterance falls
  back to CPU.
- **Context:** 4096 tokens by default. Conversation history truncated by
  oldest turns.
- **System prompt:** "You are Jessica, a helpful voice assistant running
  entirely on a Raspberry Pi. You are designed to assist blind and visually
  impaired users. Answers are short (one or two sentences) unless the user
  asks for detail. Never invent tool outputs."
- **Streaming:** tokens stream to TTS so the speaker starts before the
  reply is finished, hiding latency.

## Stage 7 — TTS (`blazend-tts`, **Rust**)

- **Library:** [Piper](https://github.com/rhasspy/piper) (C++, ONNX) via
  [`piper-rs`](https://crates.io/crates/piper-rs) — thin Rust wrapper
  over the Piper C++ engine, with streaming PCM out the Rust side.
- **Default voice per language:**
  - EN: `en_US-lessac-medium`
  - PL: `pl_PL-darkman-medium`
- Both voices stay warm in RAM (~150 MB combined) and are selected per
  utterance based on the detected reply language.
- **Mode:** streaming PCM at 22.05 kHz int16 — chunks emitted to
  `audio-out` as they are synthesised.
- **Interrupt:** "stop talking" / "przestań mówić" / "Jessica, stop" kills the TTS process
  group via `SIGTERM` for instant cutoff.
- **Pronunciation overrides:** `configs/tts.yaml: pronunciation_overrides`
  rewrites loanwords (SSH, Wi-Fi, ASR, LLM, Hailo) per language so the
  Polish voice doesn't trip on technical acronyms.

## Stage 8 — Audio playback (`blazend-audio-out`, **Rust**)

- **Crate:** `cpal` + `rodio` for mixing.
- **Mixing:** earcons (beeps for wake, error) mixed with TTS via PipeWire
  loopback. Ducking when ASR sees user start speaking.

## End-to-end latency budgets

See [`01-ARCHITECTURE.md`](01-ARCHITECTURE.md). Reproduced here for
quick reference:

| Stage                              | Pi 5 CPU | Pi 5 + Hailo-10H |
|------------------------------------|---------:|-----------------:|
| Wake-word detection                | 30 ms    | 30 ms            |
| VAD end-of-utterance               | 150 ms   | 150 ms           |
| ASR (5 s utterance)                | 700 ms   | 700 ms           |
| LLM first token                    | 350 ms   | 120 ms           |
| LLM 40-token reply                 | 1.5 s    | 600 ms           |
| TTS first audio                    | 80 ms    | 80 ms            |
| **wake → first TTS sample**        | **~1.3 s** | **~0.9 s** |

## Failure handling per stage

`blazend-health` (the watchdog) tracks per-unit liveness against
`configs/system.yaml: voice_recovery_thresholds` and emits a **`health.status`**
verdict — `ok` / `degraded` / `recovery` / `critical`. The orchestrator turns
the verdict into the LED colour + a bilingual (Polish-first) spoken cue
(`blazend/recovery.py`); SSH is already on, so `recovery`/`critical` escalate
the LED + announcement rather than opening the admin channel.

| Level | Trigger | LED | Spoken cue (PL / EN) | Action |
|-------|---------|-----|----------------------|--------|
| `degraded` | a non-essential unit (e.g. brain) silent past threshold | yellow | "Coś się zacięło…" / "I'm stuck…" | restart the unit |
| `recovery` | audio-in starved (mic dead) past threshold | red | "Tryb awaryjny." / "Recovery mode." | hold through cooldown |
| `critical` | unrecoverable (e.g. corrupt model) | red | "Błąd krytyczny." / "Critical error." | reboot into recovery image |

| Stage | If it fails                                       |
|-------|---------------------------------------------------|
| Audio-in | Watchdog → `recovery` (mic dead); LED red; spoken "recovery mode". |
| Wake  | Use the HAT user button on GPIO17 if configured.  |
| VAD   | Fall back to fixed 4 s capture window.            |
| ASR   | Reprompt; second failure switches to `tiny` model. |
| NLU   | Pass through to brain; brain replies "I'm not sure I understood." |
| Brain | Watchdog → `degraded`; orchestrator says "I'm stuck…"; restart. |
| TTS   | Switch to second voice; if still silent, play "error" earcon. |
| Audio-out | Watchdog restarts the unit; LED red. |

## Hot-path optimisations

1. **Pre-roll buffer.** When wake fires, we feed the previous 0.5 s of
   audio to ASR so the user's first phoneme isn't clipped.
2. **Parallel TTS warmup.** As soon as the brain emits the first token,
   TTS pre-loads the voice model so the first audio chunk costs only
   synthesis time, not load time.
3. **Wake-word cooldown.** Prevents the wake model from firing on the
   system's own TTS output (LED + earcon are not enough — we also gate
   with PipeWire loopback ducking).
