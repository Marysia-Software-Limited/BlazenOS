# blazen_os — Personal assistant for the blind

**Voice-first Linux distribution for Raspberry Pi 5.** The appliance twin of **Jessica**, a voice-first personal assistant designed specifically for **blind and visually impaired users**.

No keyboard, no monitor. The user speaks; Jessica listens, thinks, answers, reads things aloud, drafts replies, takes notes, surfaces the morning briefing, plays podcasts, integrates with Gemini for deep-research questions, and learns the user's voice over time.

The LLM runs on the Pi 5 CPU by default; an optional Raspberry Pi AI HAT+ (Hailo) accelerates it. The product is **bilingual EN + PL from M1** and Jessica responds to **"hey Jessica" / "Jess" / "hej Jessico"**. SSH is reserved for break-glass administration only.

> **Monorepo mobile twins:** native iOS ([`ios/`](ios/), SwiftUI) and
> Android ([`android/`](android/), Jetpack Compose) live alongside the
> Pi 5 appliance in this repo. They share the Rust mobile core
> ([`crates/jessica-core`](crates/jessica-core/),
> [`crates/jessica-ffi`](crates/jessica-ffi/)). The Flutter prototype at
> [`../rachel/`](../rachel/) is a **reference implementation of the
> cross-platform contract**, not the shipping product. See
> [`docs/17-MOBILE-MONOREPO.md`](docs/17-MOBILE-MONOREPO.md) and
> [`docs/product/09-MOBILE-PLATFORM-DECISION.md`](docs/product/09-MOBILE-PLATFORM-DECISION.md).

> **Status:** **M0 done, M1 partial.** Bilingual Python + Rust
> skeleton runs end-to-end. **Linux** (Arch on `paul`) is the primary
> dev rig — handles `make vm-image`, `make rust-aarch64`, and the full
> test pyramid. **macOS** is the secondary inner-loop rig
> (`make dev`, `make test-fast`, `make qemu-smoke`). See
> [`docs/15-DEV-WORKFLOW.md`](docs/15-DEV-WORKFLOW.md) and
> [`docs/10-ROADMAP.md`](docs/10-ROADMAP.md).

> **Target hardware:** Raspberry Pi 5 — **16 GB reference**, 8 GB supported
> secondary. Optional accelerators: AI HAT+ 13T / 26T / 10H. See
> [`docs/02-HARDWARE.md`](docs/02-HARDWARE.md) and
> [`docs/12-ML-ACCELERATOR.md`](docs/12-ML-ACCELERATOR.md).

> **Spoken languages:** English and Polish are co-equal first-class
> targets. Auto-detection per utterance; voice-pinnable. See
> [`docs/13-LANGUAGES.md`](docs/13-LANGUAGES.md).

> **Implementation languages:** **Python + Rust**. Audio I/O, wake word,
> TTS, watchdog and IPC live in Rust; the orchestrator, ASR, LLM,
> bootstrap and config layer live in Python. The boundary is the IPC
> wire format. See [`docs/14-RUST-PYTHON-SPLIT.md`](docs/14-RUST-PYTHON-SPLIT.md).

---

## TL;DR (PL)

System operacyjny `blazen_os` to zbudowana na Raspberry Pi OS Lite
(Bookworm 64-bit) dystrybucja dla **Raspberry Pi 5**, w której
**całe codzienne UI to głos**, a głównym celem jest pomoc osobom
niewidomym i słabowidzącym:

1. Mikrofon ciągle nasłuchuje słowa wybudzającego (wake word).
2. Po wybudzeniu lokalny model ASR (Whisper przez `faster-whisper`)
   zamienia mowę na tekst.
3. Lokalny LLM (domyślnie Qwen 2.5 3B Q4 na llama.cpp; opcjonalnie
   wariant `.hef` na akceleratorze Hailo) decyduje o intencji i
   generuje odpowiedź — bez internetu.
4. Lokalny silnik TTS (Piper) odpowiada syntezą mowy.
5. SSH istnieje wyłącznie do awaryjnej rekonfiguracji (np. zmiana sieci
   Wi-Fi, przywrócenie systemu) — domyślnie wyłączony, włączany głosem
   lub przez automatyczny tryb recovery.

Opcjonalny akcelerator ML (Raspberry Pi AI HAT+, najlepiej **Hailo-10H**)
przyspiesza konwersację LLM-em: **TTFT spada z ~350 ms (CPU) do ~120 ms
(Hailo-10H)** a tempo generacji rośnie z ~12 do ~35 tokenów/s. Cały
kontrakt funkcjonalny działa bez akceleratora — to tylko strict-improvement
ścieżka wydajnościowa, opisana w
[`docs/12-ML-ACCELERATOR.md`](docs/12-ML-ACCELERATOR.md).

Testy wszystkich iteracji odbywają się w **QEMU** (machine `raspi4b` z
mnożnikiem latencji aż upstream QEMU obsłuży `raspi5b`), z syntetyzowanymi
plikami audio jako wejściem i nagrywanym wyjściem TTS jako oczekiwanym
artefaktem. Pozwala to uruchamiać E2E testy w CI bez dostępu do fizycznego
sprzętu. Weryfikacja ścieżki akceleratora wymaga prawdziwego Pi 5 +
AI HAT+ (Tier 4 — patrz [`docs/08-TESTING.md`](docs/08-TESTING.md)).

---

## What you actually get

| Layer | Component | Notes |
|---|---|---|
| Base OS | Raspberry Pi OS Lite 64-bit (Bookworm) | No GUI. Read-only `/usr` (overlayfs) by default. |
| Audio HAL | ALSA + PipeWire | USB mic or ReSpeaker HAT. |
| Wake word | [openWakeWord](https://github.com/dscripka/openWakeWord) | Two models loop in parallel: `hey jessica` (EN) + `hej jessico` (PL). |
| ASR | [faster-whisper](https://github.com/SYSTRAN/faster-whisper) | Multilingual `small` default — EN + PL out of the box. |
| VAD | [silero-vad](https://github.com/snakers4/silero-vad) | End-of-utterance detection. |
| LLM (CPU) | [llama.cpp](https://github.com/ggml-org/llama.cpp) | Qwen 2.5 3B Q4 default — multilingual EN/PL. See [docs/05-MODELS.md](docs/05-MODELS.md). |
| LLM (accel) | [HailoRT](https://github.com/hailo-ai/hailort) | Optional `.hef` runtime on AI HAT+ — see [docs/12-ML-ACCELERATOR.md](docs/12-ML-ACCELERATOR.md). |
| TTS | [Piper](https://github.com/rhasspy/piper) | Two voices warm: `en_US-lessac-medium` + `pl_PL-darkman-medium`. <100 ms first audio. |
| Orchestrator | `blazend` (Python systemd service) | See [docs/01-ARCHITECTURE.md](docs/01-ARCHITECTURE.md). |
| Config | YAML under `/etc/blazen/` | Voice-mutable subset + SSH-only subset. Language-tagged intents. |
| Test rig | QEMU `aarch64` + virtual ALSA | See [docs/09-VM-TESTING.md](docs/09-VM-TESTING.md). |

---

## Repository layout

Monorepo: the **shared core** (common to iOS, Android and the Pi 5
appliance) lives at the top level; the **Raspberry Pi 5 appliance** is a
self-contained project under `rpi5/`.

```
blazen_os/
├── README.md                # this file
├── CLAUDE.md                # entrypoint for Claude Code
├── AGENTS.md                # cross-agent baseline (Codex, Junie, etc.)
├── Makefile                 # root: cross-host git sync + build/test orchestration
├── rust-toolchain.toml      # pins Rust channel for reproducibility
├── docs/                    # design docs (incl. docs/product/ — shared spec)
├── configs/                 # SHARED contract + appliance config (YAML)
│   ├── intents/system.yaml  #   shared intent vocabulary (mobile reads it too)
│   ├── vm/                  #   QEMU/VM-specific configs
│   └── _schema/events/      #   JSON Schemas for IPC events (cross-language contract)
├── scripts/                 # shared tooling: flash, bootstrap, run-vm, mobile FFI build
├── crates/                  # SHARED CORE Cargo workspace (all 3 platforms)
│   ├── blazend-ipc/         #   IPC wire / event envelope (lib)
│   ├── blazend-fabric/      #   CRDT sync log (lib + appliance binary)
│   ├── jessica-core/        #   intent router + fabric re-export
│   └── jessica-ffi/         #   C ABI + JNI over jessica-core (iOS/Android)
├── android/                 # ── Native Android app (Kotlin + Jetpack Compose)
├── ios/                     # ── Native iOS app (SwiftUI + JessicaCore Swift Package)
└── rpi5/                    # ── Raspberry Pi 5 APPLIANCE PROJECT ──
    ├── Makefile             #   forwards to the root orchestrator
    ├── pyproject.toml       #   Python project metadata + tooling
    ├── src/blazend/         #   Python: orchestrator, asr, brain, config, ...
    ├── crates/              #   appliance Cargo workspace (audio-in/out, wake, tts, health)
    ├── stage-blazen/        #   pi-gen overlay (installs everything into the image)
    └── tests/               #   scenarios, harness, audio fixtures (Tier 0-3)
```

The in-repo `ios/` and `android/` trees consume `crates/` (Rust core via
`jessica-ffi`) and `configs/` (shared contract); they never touch `rpi5/`.

---

## Quick start (developer machine, no Pi needed)

```bash
make install-deps           # host tools: qemu, python venv, rust toolchain, piper, whisper
make build                  # python venv + cargo build for blazend-* binaries
make models                 # downloads & verifies all local ML models
make dev                    # NEW: runs the full stack on the dev host (no VM, fastest iteration)
make vm-image               # builds blazen_os SD image (qcow2 for QEMU)
make run-vm                 # boots blazen_os in QEMU, exposes SSH on :2222
make test-fast              # cargo test + pytest unit/component (<2 min)
make test                   # full Tier 0..3 pyramid
make test-scenario S=01-wake-word
```

On a real Raspberry Pi 5:

```bash
make flash DEVICE=/dev/disk4    # writes the same image to SD card
# Insert SD, power on. Headless WiFi via wpa_supplicant.conf injected into boot.
# Speak the wake word once the LED indicator turns green.
# (Optional) Connect the Raspberry Pi AI HAT+ via PCIe FFC before powering on
# and blazen_os auto-detects + uses it for the LLM. See docs/12-ML-ACCELERATOR.md.
```

---

## Mobile twins (in this monorepo)

| Tree                       | Purpose                                              |
|----------------------------|------------------------------------------------------|
| [`android/`](android/)     | Native Android app — Kotlin 2.0, Compose, AGP 8.7, minSdk 30, target 35. Two modules: `:app`, `:core`. |
| [`ios/`](ios/)             | Native iOS app — Swift 6.0, SwiftUI strict concurrency, iOS 17.0+, XcodeGen-driven project. Two targets: `Jessica`, `JessicaCore` (Swift Package). |
| [`crates/jessica-core`](crates/jessica-core/) | Shared business logic — intent router, sync log (CRDT), adapter contracts. Pure Rust, no platform deps. |
| [`crates/jessica-ffi`](crates/jessica-ffi/) | C ABI (cbindgen → `jessica_ffi.h` → `JessicaFFI.xcframework`) + JNI (`libjessica_ffi.so`). |

Mobile dev rig is **paul** (Linux) — see
[`docs/15-DEV-WORKFLOW.md`](docs/15-DEV-WORKFLOW.md). iOS final build /
signing / TestFlight still needs a Mac; Android builds end-to-end on
paul.

```bash
cd android/ && make build         # ./gradlew assembleDebug
cd ios/     && make test          # JessicaCoreTests via swift test
```

---

## Where to read next

1. [`docs/01-ARCHITECTURE.md`](docs/01-ARCHITECTURE.md) — high-level diagram + processes.
2. [`docs/02-HARDWARE.md`](docs/02-HARDWARE.md) — exact BOM and tested microphones.
3. [`docs/04-VOICE-PIPELINE.md`](docs/04-VOICE-PIPELINE.md) — wake → ASR → LLM → TTS flow.
4. [`docs/12-ML-ACCELERATOR.md`](docs/12-ML-ACCELERATOR.md) — optional Hailo accelerator for LLM.
5. [`docs/13-LANGUAGES.md`](docs/13-LANGUAGES.md) — EN + PL spoken contract.
6. [`docs/14-RUST-PYTHON-SPLIT.md`](docs/14-RUST-PYTHON-SPLIT.md) — implementation language boundary.
7. [`docs/15-DEV-WORKFLOW.md`](docs/15-DEV-WORKFLOW.md) — paul (Linux) primary rig + monorepo workflow.
8. [`docs/17-MOBILE-MONOREPO.md`](docs/17-MOBILE-MONOREPO.md) — how Pi5, Android, and iOS share the Rust core in this repo.
9. [`docs/product/09-MOBILE-PLATFORM-DECISION.md`](docs/product/09-MOBILE-PLATFORM-DECISION.md) — why native, why shared Rust core.
10. [`docs/10-ROADMAP.md`](docs/10-ROADMAP.md) — milestone plan M0..M10.
11. [`docs/11-CLAUDE-PLAYBOOK.md`](docs/11-CLAUDE-PLAYBOOK.md) — how Claude works on this repo.

---

## License

TBD — likely Apache-2.0 for the orchestrator, with model weights under their
respective upstream licenses (Whisper: MIT, Piper: MIT, Llama 3.2: Llama
Community License, Qwen2.5: Apache-2.0).
