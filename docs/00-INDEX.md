# Documentation index

Read top-to-bottom on first contact. Afterwards, re-read only what changed.

| #   | File                                              | Purpose |
|-----|---------------------------------------------------|---------|
| 00  | [`00-INDEX.md`](00-INDEX.md)                      | This file. |
| 01  | [`01-ARCHITECTURE.md`](01-ARCHITECTURE.md)        | High-level diagram, processes, IPC contract. |
| 02  | [`02-HARDWARE.md`](02-HARDWARE.md)                | BOM, supported microphones/speakers, GPIO. |
| 03  | [`03-SOFTWARE-STACK.md`](03-SOFTWARE-STACK.md)    | Base OS, init system, package pins. |
| 04  | [`04-VOICE-PIPELINE.md`](04-VOICE-PIPELINE.md)    | Wake → VAD → ASR → NLU → LLM → TTS flow with budgets. |
| 05  | [`05-MODELS.md`](05-MODELS.md)                    | Concrete ML model choices, sizes, licensing. |
| 06  | [`06-SSH-BOOTSTRAP.md`](06-SSH-BOOTSTRAP.md)      | Headless first-boot, SSH access policy (on by default, pubkey-only). |
| 07  | [`07-CONFIGURATION.md`](07-CONFIGURATION.md)      | YAML config schema, voice-vs-SSH split. |
| 08  | [`08-TESTING.md`](08-TESTING.md)                  | Five-tier test pyramid, scenarios, CI. |
| 09  | [`09-VM-TESTING.md`](09-VM-TESTING.md)            | QEMU image, virtual audio, golden audio fixtures. |
| 10  | [`10-ROADMAP.md`](10-ROADMAP.md)                  | Milestones M0..M10 with exit criteria. |
| 11  | [`11-CLAUDE-PLAYBOOK.md`](11-CLAUDE-PLAYBOOK.md)  | Operational guide for Claude working on this repo. |
| 12  | [`12-ML-ACCELERATOR.md`](12-ML-ACCELERATOR.md)    | Optional Hailo AI HAT+ / Hailo-10H integration for the LLM path. |
| 13  | [`13-LANGUAGES.md`](13-LANGUAGES.md)              | **Spoken** languages — PL + EN bilingual contract (Polish-first). |
| 14  | [`14-RUST-PYTHON-SPLIT.md`](14-RUST-PYTHON-SPLIT.md) | **Implementation** languages — which components are Python, which are Rust, and why. |
| 15  | [`15-DEV-WORKFLOW.md`](15-DEV-WORKFLOW.md)        | Linux (`paul`) vs macOS hybrid workflow; what runs where. |
| 16  | [`16-SYNC-PROTOCOL.md`](16-SYNC-PROTOCOL.md)      | **Bidirectional sync** — how paul Claude and macOS Claude exchange changes; what crosses the shared boundary. |
| 17  | [`17-MOBILE-MONOREPO.md`](17-MOBILE-MONOREPO.md)  | **Monorepo layout** — Pi 5 + Android + iOS + shared Rust core in one tree. The map. |
| 18  | [`18-PROTOTYPE.md`](18-PROTOTYPE.md)              | **Working prototype** — `make demo`: name reaction, Polish chat, Gemini news, memory + reminders. |
| 19  | [`19-DOMAIN-ARCHITECTURE.md`](19-DOMAIN-ARCHITECTURE.md) | **Domain architecture** — the 6 capability domains; portable cores as common libraries at the repo-root `domains/` tree, platform-specific adapters under `rpi5/`/`android/`/`ios/`; mind/body split; the 5-phase program. **Canonical layout doc.** |
| 20  | [`20-LINUX-INSTALLER.md`](20-LINUX-INSTALLER.md) | **Common Linux installer** (`installer/`) — the full voice pipeline natively on Arch/Debian/Fedora; desktop (user units, XDG) vs appliance modes; runtime Jabra-preferred/ALSA-fallback audio detection; the CUDA profile (large-v3-turbo + Ollama Bielik 11B); macOS seam stubbed. |

## Android & iOS (mobile twins in this monorepo)

| Path | Purpose |
|------|---------|
| [`/android/README.md`](../android/README.md) | Android project entry point — quickstart, layout, status |
| [`/android/docs/architecture.md`](../android/docs/architecture.md) | `:app`/`:core` module split, Rust core seam, M0 vs M1 |
| [`/android/docs/build.md`](../android/docs/build.md) | Toolchain, Gradle, cargo-ndk wiring (M1), signing |
| [`/android/docs/ml-stack.md`](../android/docs/ml-stack.md) | openWakeWord, on-device Speech, Gemini Nano (AICore), TTS |
| [`/ios/README.md`](../ios/README.md) | iOS project entry point — quickstart, layout, status |
| [`/ios/docs/architecture.md`](../ios/docs/architecture.md) | Jessica + JessicaCore split, FFI seam, M0 vs M1 |
| [`/ios/docs/build.md`](../ios/docs/build.md) | XcodeGen, swift test, M1 xcframework pipeline |
| [`/ios/docs/ml-stack.md`](../ios/docs/ml-stack.md) | openWakeWord, Speech framework, Foundation Models, TTS |
| [`product/09-MOBILE-PLATFORM-DECISION.md`](product/09-MOBILE-PLATFORM-DECISION.md) | **Mobile platform decision** — Native Swift/Kotlin + Rust core. |
| [`product/10-MOBILE-HARDWARE.md`](product/10-MOBILE-HARDWARE.md) | **Mobile hardware** — Reference iPhones, Pixels, and accessories. |
| [`product/15-NATIVE-MIGRATION.md`](product/15-NATIVE-MIGRATION.md) | **Native migration** — Plan for moving from Flutter to native mobile apps. |

## Cross-implementation product spec

| Path | Purpose |
|------|---------|
| [`product/00-INDEX.md`](product/00-INDEX.md) | **Shared Jessica product spec** — read by both `blazen_os` (this project, Pi 5 appliance) and [`rachel`](../../rachel/) (mobile twin, iOS + Android). Source of truth for persona, intents, integrations, briefing, privacy, mobile platform + hardware choice. |

## Findings (investigation reports)

| File | Purpose |
|------|---------|
| [`findings/wake-word-wm8960.md`](findings/wake-word-wm8960.md) | **Wake word not viable on the WM8960 HAT mic** — Vosk/Whisper/embedding/DTW all fail to discriminate "Dżesika"; root cause is mic audio quality. Reproducible recipe for a better mic. |

## Document conventions

- **Decisions** are recorded inline with a `> **Decision (YYYY-MM-DD):**` block.
  Anything else is description, not a binding decision.
- **Budgets** (latency, RAM, model size) use a table at the top of the relevant
  doc so future agents can fail builds when violated.
- **Cross-references** use the `[NN-NAME.md](NN-NAME.md)` form so renames
  surface as broken links.
