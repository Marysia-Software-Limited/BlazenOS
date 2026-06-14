# AGENTS.md — `blazen_os` cross-agent baseline

This file is the **canonical operating contract** for any LLM/agent harness
working on `blazen_os` (Claude Code, OpenAI Codex, Junie, Cursor, etc.).
Harness-specific entrypoints (e.g., `CLAUDE.md`) extend this file with
tool-specific operational notes; when they disagree with this file, the
**most restrictive** guidance wins unless the user explicitly overrides.

---

## 1. Product invariants (do not violate)

1. **Voice-only daily UX.** A normal user has no keyboard, no monitor, no
   mouse. **Jessica is designed as a personal assistant for blind and visually impaired persons.** Everything routine — including reading the news, asking a
   question, changing the volume, restarting a service — must be reachable
   by voice.
2. **On-device ML.** No internet round-trip during ASR, intent routing, LLM
   reasoning, or TTS. Optional online tools (e.g., a weather plugin) are
   gated behind explicit user consent and run as separate processes.
3. **SSH is on by default** — pubkey-only and fail-closed (no key or
   password ships; the operator provisions a key). It complements the voice
   path for advanced admin (e.g., changing Wi-Fi SSID, recovering the image)
   but is not the daily-use surface. See `docs/06-SSH-BOOTSTRAP.md`.
4. **Reproducible image builds.** Any image we ship to a real Pi must be
   buildable from this repo's `make vm-image` / `make pi-image` targets,
   with pinned model checksums.
5. **No regressions in the voice latency budget.** End-to-end (wake → TTS
   first sample) latency budget is documented in
   [`docs/04-VOICE-PIPELINE.md`](docs/04-VOICE-PIPELINE.md). Any change that
   pushes the median over budget is a bug, not an accepted trade-off, until
   explicitly negotiated.
6. **Pi 5 is the only target.** Raspberry Pi 5 **16 GB is the reference**
   platform; 8 GB is a supported secondary target (smaller ASR/LLM
   variants). Older Pis are out of scope. See `docs/02-HARDWARE.md`.
7. **CPU is the LLM contract.** The Hailo accelerator path
   ([`docs/12-ML-ACCELERATOR.md`](docs/12-ML-ACCELERATOR.md)) is an
   optional strict-improvement layer; every feature must work without it.
8. **Polish-first; English co-equal.** The prototype is bilingual from M1
   with Polish as the primary language (default, first-listed); English
   stays parity-required. Every user-facing voice surface ships in both —
   new intents, phrases, and scenarios are incomplete without their EN or
   PL counterpart. See
   [`docs/13-LANGUAGES.md`](docs/13-LANGUAGES.md).
9. **Python + Rust only for the Pi 5 appliance.** Audio I/O, wake word,
   TTS, watchdog and IPC are Rust; orchestrator, ASR, LLM, bootstrap and
   config are Python. New Pi 5 components pick one per the rules in
   [`docs/14-RUST-PYTHON-SPLIT.md`](docs/14-RUST-PYTHON-SPLIT.md). Cross-
   language calls go over the IPC contract, never through inline FFI.
   Shell is permitted for boot/installer scripts; C/C++ only inside
   upstream FFI shims.
10. **Native per platform on mobile.** The Android tree ([`android/`](android/))
    is Kotlin + Jetpack Compose. The iOS tree ([`ios/`](ios/)) is Swift +
    SwiftUI. They share business logic via the Rust crates
    [`crates/jessica-core/`](crates/jessica-core/) and
    [`crates/jessica-ffi/`](crates/jessica-ffi/) — see
    [`docs/17-MOBILE-MONOREPO.md`](docs/17-MOBILE-MONOREPO.md). No third
    UI stack enters the mobile core; no business logic gets re-implemented
    in Swift or Kotlin if it can live in Rust.

---

## 2. Repository conventions

- **Source-of-truth docs** live under `docs/` (cross-cutting) and
  `<surface>/docs/` (per-surface). Anything that contradicts them in code
  or configs is a bug.
- **Configs** are YAML under `configs/`. Runtime reads `/etc/blazen/`
  on the device; the build pipeline copies `configs/*` there.
- **Tests** are scenario-driven (`rpi5/tests/scenarios/*.yaml`). Adding an
  intent means adding a scenario.
- **Generated test artifacts** go under `_test_projects/` or
  `rpi5/tests/fixtures/` — gitignored. Never scatter scratch dirs.
- **Models** are downloaded by `make models`, written to `./models/`,
  gitignored, and checksummed in `configs/llm.yaml` / `configs/asr.yaml` /
  `configs/tts.yaml`.
- **Monorepo surfaces** each own their `README.md`, `AGENTS.md`,
  `CLAUDE.md`, and `Makefile` and never override behaviour in another
  surface. See [`docs/17-MOBILE-MONOREPO.md`](docs/17-MOBILE-MONOREPO.md).

---

## 3. The maintenance scenario ("let do maintenance" / "test-fix loop")

1. Finish the requested implementation first.
2. `make test-fast` (Tier 0 + 1 — unit + component with mocks).
3. `make test-vm` (Tier 2 + 3 — pipeline + scenarios in QEMU).
4. Security/safety pass — `make audit` (deps + image config lint).
5. Code cleanup pass — remove dead code, simplify, keep style consistent.
6. Re-run 2-4 until green.
7. **Only then** update docs and rules:
   - `README.md`, `AGENTS.md`, `CLAUDE.md`
   - `docs/*.md` for anything that changed behaviour
   - `configs/*.yaml` defaults if the contract changed
   - `rpi5/tests/scenarios/*.yaml` if expected behaviour shifted

---

## 4. Read-first order for any agent

1. `AGENTS.md` (this file)
2. Harness-specific entrypoint if present (e.g., `CLAUDE.md`)
3. `README.md`
4. `docs/00-INDEX.md` → walk the docs in numbered order on first contact;
   thereafter re-read only what changed.

---

## 5. Default editing rules

1. **Read before edit.** No blind writes.
2. **Surgical diffs.** No drive-by refactors.
3. **Tests cannot be weakened** to make them pass.
4. **Configs and docs move together.** A config default change without a
   doc update is a half-finished commit.
5. **Don't commit binaries.** Models, audio fixtures, VM images are
   regenerated by `make` targets and stay out of git.
6. **One milestone at a time.** Don't pre-implement future milestones; the
   roadmap exists so we ship incrementally.
7. **Right rig for the job.** `paul` (Linux) is the **primary** rig
   for the whole monorepo — Pi 5 image builds, cross-compile, full Tier
   2-3 tests, Android builds (gradle + adb), Rust core for the shared
   mobile FFI, and all doc edits. The maintainer's Mac is required only
   for the final iOS xcodebuild / TestFlight cut (because `xcodebuild`
   doesn't run on Linux). See [`docs/15-DEV-WORKFLOW.md`](docs/15-DEV-WORKFLOW.md)
   and [`docs/17-MOBILE-MONOREPO.md`](docs/17-MOBILE-MONOREPO.md).

---

## 6. Verification checklist (before declaring a task done)

- [ ] `make test-fast` green for the affected paths.
- [ ] `make test-vm` green for the affected scenarios.
- [ ] Docs reflect new behaviour (in the same commit).
- [ ] No model weights, audio fixtures, or VM images staged.
- [ ] Cross-agent rule files agree.
- [ ] New voice intents have scenarios under `rpi5/tests/scenarios/`.
- [ ] Latency budget unchanged or explicitly renegotiated.
