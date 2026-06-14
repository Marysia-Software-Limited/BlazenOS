# Claude Code Rules — `blazen_os`

This file is the entrypoint Claude Code loads automatically for this project.
Read [`AGENTS.md`](AGENTS.md) first for the cross-agent baseline that applies
to every LLM harness (Claude, Codex, Junie, etc.); this file adds Claude-only
operational notes.

When `AGENTS.md` and this file disagree, prefer the **most restrictive**
guidance unless the user explicitly overrides it. When in doubt, ask.

## 1. Read These Files First (in order)

1. `/CLAUDE.md` (this file)
2. `/AGENTS.md` (cross-agent baseline)
3. `/README.md` (user-facing project overview)
4. `docs/00-INDEX.md` (documentation map)
5. `docs/01-ARCHITECTURE.md` → `docs/11-CLAUDE-PLAYBOOK.md` (read in order
   the first time you touch the repo; afterwards re-read only what changed)
6. `docs/17-MOBILE-MONOREPO.md` (monorepo map — Pi 5 + Android + iOS)

When working inside `android/` or `ios/`, also load that surface's own
`README.md`, `AGENTS.md`, `CLAUDE.md`, and `docs/architecture.md`.

Whenever the user says "rules changed" or you modify a doc, refresh these.

## 2. Project Snapshot

- **One-line:** monorepo for the **Jessica** voice-first assistant —
  Raspberry Pi 5 appliance ([`rpi5/`](rpi5/)), native Android
  ([`android/`](android/)), native iOS ([`ios/`](ios/)), and a shared
  Rust core ([`crates/jessica-core`](crates/jessica-core/),
  [`crates/jessica-ffi`](crates/jessica-ffi/), [`crates/blazend-ipc`](crates/blazend-ipc/),
  [`crates/blazend-fabric`](crates/blazend-fabric/)). Pi 5 is **16 GB
  reference**, 8 GB supported secondary; optional Hailo accelerator on
  the LLM path. Fully on-device ML across all three surfaces. SSH on
  the Pi is **on by default** (pubkey-only, no shipped credential). See
  [`docs/17-MOBILE-MONOREPO.md`](docs/17-MOBILE-MONOREPO.md).
- **Phase:** **M0 scaffolding** — design docs, configs, scripts, and
  test harness skeletons for the Pi 5 surface under `rpi5/`; the mobile
  trees ship Kotlin and Swift placeholders that exercise the shared
  Rust API contract end-to-end. No bootable Pi image and no signed
  mobile builds yet.
- **Monorepo layout (2026-06-11):** the repo root holds the **shared
  core** common to all three platforms — `crates/` (Rust:
  `blazend-ipc`, `blazend-fabric`, `jessica-core`, `jessica-ffi`),
  `configs/` (shared contract + appliance config), `docs/`, `scripts/`.
  The **Raspberry Pi 5 appliance** is a self-contained project under
  **`rpi5/`** (Python `rpi5/src/blazend`, appliance crates
  `rpi5/crates/*`, `rpi5/stage-blazen`, `rpi5/tests`). The
  `android/` and `ios/` trees consume `crates/` + `configs/`. Full tree
  in [`docs/14-RUST-PYTHON-SPLIT.md`](docs/14-RUST-PYTHON-SPLIT.md) §4
  and [`docs/17-MOBILE-MONOREPO.md`](docs/17-MOBILE-MONOREPO.md).
- **Five things that must always be true:**
  1. The system is usable with **zero peripherals beyond a USB mic + speaker**.
     If a change forces a keyboard/monitor for daily use, reject it.
  2. ML inference runs **on-device**. No outbound calls to cloud LLMs/ASRs
     during normal operation. (Telemetry off by default; opt-in only.)
  3. SSH is **on by default** — pubkey-only, fail-closed (no key/password
     ships; the operator provisions a key). It complements the voice path;
     it is not the daily-use surface. See
     [`docs/06-SSH-BOOTSTRAP.md`](docs/06-SSH-BOOTSTRAP.md).
  4. The **CPU path is the contract**. Any feature relying on the optional
     Hailo accelerator must degrade gracefully to CPU. The accelerator is
     a strict-improvement path, never a precondition. See
     [`docs/12-ML-ACCELERATOR.md`](docs/12-ML-ACCELERATOR.md).
  5. **Polish is the primary language; English is co-equal.** Polish leads
     everywhere (default, first-listed, examples-first), but English remains
     a parity-required first-class language: every user-facing voice surface —
     wake word, intents, confirmations, replies, error tones, system messages —
     ships in both. A change that lands a Polish-only **or** English-only
     intent / phrase / scenario is incomplete until the counterpart exists.
     See [`docs/13-LANGUAGES.md`](docs/13-LANGUAGES.md).
  6. **Python and Rust are the two implementation languages for the
     Pi 5 surface.** Hot loops, audio I/O, wake word, TTS, watchdog
     and the IPC wire are **Rust**. Orchestrator, ASR, LLM, bootstrap
     and config are **Python**. No third language enters the Pi 5
     stack. Cross-language calls go through the IPC contract, never
     through FFI inside a Pi 5 component. See
     [`docs/14-RUST-PYTHON-SPLIT.md`](docs/14-RUST-PYTHON-SPLIT.md).
  7. **Native per platform on mobile.** [`android/`](android/) is
     Kotlin + Compose; [`ios/`](ios/) is Swift + SwiftUI. They share
     business logic via the Rust mobile core
     ([`crates/jessica-core`](crates/jessica-core/) +
     [`crates/jessica-ffi`](crates/jessica-ffi/)), and ML is the OS's
     job (Apple Speech / Foundation Models on iOS, Google Speech /
     Gemini Nano on Android). See
     [`docs/17-MOBILE-MONOREPO.md`](docs/17-MOBILE-MONOREPO.md) and
     [`docs/product/09-MOBILE-PLATFORM-DECISION.md`](docs/product/09-MOBILE-PLATFORM-DECISION.md).

## 3. Mandatory Test Artifact Location

Any generated test projects, audio fixtures, or scratch builds go under
`rpi5/tests/fixtures/` or `_test_projects/` (gitignored). **Never** scatter test
artifacts across the repo.

## 4. Default Maintenance Workflow ("let do maintenance")

1. `make test-fast` — runs Tier 0 (unit) + Tier 1 (component integration with
   mocked audio/LLM). Should be <60 s on a developer laptop.
2. `make test-vm` — runs Tier 2-3 in QEMU (boots image, plays synthetic
   audio, asserts on transcripts and TTS output). 5-15 min depending on
   model sizes.
3. Investigate every failure with **surgical** fixes — never weaken or skip
   a failing scenario without an explicit "skip with reason" annotation.
4. Re-run until green.
5. Only **after** the test pyramid is green, sync the docs:
   - `README.md`, `AGENTS.md`, this file
   - `docs/*.md` for any behaviour change
   - `configs/*.yaml` defaults if the contract changed
   - `rpi5/tests/scenarios/*.yaml` if expected behaviour shifted

For long-running maintenance, use `make test-soak` (24-hour run inside the
VM). Surface only the failures.

## 5. Claude Code Operational Notes

1. **Plan before non-trivial edits.** Anything that touches the voice pipeline,
   model selection, or VM image build must go through `EnterPlanMode` first.
2. **Track multi-step work with `TaskCreate`/`TaskUpdate`.** Mark
   `in_progress` before starting, `completed` immediately after — never batch.
3. **Prefer dedicated tools** (`Read`, `Edit`, `Write`, `Glob`, `Grep`) over
   shelling out. Use `Bash` only for `make`, `qemu`, `git`, and similar.
4. **Use the `Explore` subagent** for repo-wide reconnaissance. Use `Plan` for
   design work. Spawn `general-purpose` agents for parallel independent
   investigations (e.g., simultaneously evaluating two LLM choices).
5. **Confirm before risky actions:** flashing real SD cards, pushing branches,
   force-push, `git reset --hard`, deleting `rpi5/tests/fixtures/audio/` (large
   regen cost), modifying `configs/system.yaml` defaults.
6. **Dev rig.** Linux (`paul`) is the **primary rig for the whole
   monorepo** — Pi 5, Android, iOS sources, Rust core, all docs. `paul`
   is **Arch Linux running under WSL2 on Windows 11** (x86_64), not bare
   metal: aarch64 QEMU is **TCG-only** (no KVM accel → `make run-vm` is
   slow and the full Pi boot is blocked, see `docs/10-ROADMAP.md` M1),
   real-SD flashing needs `wsl --mount` (or the Windows Imager), and USB
   mic/HAT bring-up needs `usbipd-win`. The maintainer's Mac is required
   only for the final iOS xcodebuild / TestFlight cut. See
   [`docs/15-DEV-WORKFLOW.md`](docs/15-DEV-WORKFLOW.md) (§ WSL2 host notes)
   and [`docs/17-MOBILE-MONOREPO.md`](docs/17-MOBILE-MONOREPO.md) §4.

   **If you (Claude) are running on `paul`:** this is your home repo.
   Pi 5 image builds, cross-compile, Tier 2-3 tests, Android gradle
   builds + adb, Rust core changes — all yours. For iOS, you edit
   Swift / project.yml / docs and run `swift test` for `JessicaCore`,
   but flag any task that needs `xcodebuild` as "needs Mac" in the
   summary.

   **If you (Claude) are running on macOS:** drive the final iOS
   build (`xcodebuild`, signing, TestFlight). Everything else is
   already covered by paul; sync via git.
6. **Memory hygiene.** Repo structure is derivable — do not memorize file
   layouts. Persist only genuinely surprising user preferences or
   non-obvious project facts.

## 6. Editing Conventions for Claude

1. Always `Read` a file before editing it.
2. Minimal, surgical diffs. Do not refactor unrelated code.
3. Do not modify upstream-managed files in-place — wrap or override:
   - Raspberry Pi OS Lite base image artifacts (`pi-gen` outputs)
   - Whisper / Piper / llama.cpp source trees if vendored
4. Never weaken a failing test. Investigate root causes.
5. Never commit:
   - `models/` (large ML weights — git-lfs only if explicitly enabled)
   - `rpi5/tests/fixtures/audio/*.wav` (regen via `make audio-fixtures`)
   - `_test_projects/`, `.venv/`, `vm-images/*.qcow2`
   - `target/` (Cargo build output)
6. Update `AGENTS.md` and this file together when cross-agent rules change.
7. **Language choice for new code:**
   - **Pi 5 surface:** check
     [`docs/14-RUST-PYTHON-SPLIT.md`](docs/14-RUST-PYTHON-SPLIT.md) §1
     before adding a new component. If a Rust component needs to do
     "just one Python thing", that's a sign to expose it as a separate
     blazend-* unit and let the IPC contract carry the call.
   - **Mobile surfaces:** any business logic that the Pi 5 also needs,
     or that's worth sharing across iOS+Android, goes into
     `crates/jessica-core/`. UI and ML-glue go in Kotlin
     (`android/`) and Swift (`ios/`). Don't reimplement a Rust crate
     in Kotlin or Swift.
8. **When you add a new IPC event:** add the JSON Schema under
   `configs/_schema/events/` and regenerate types in both languages via
   `make gen-events`. The schema is the source of truth, not the
   hand-written Python or Rust type.
9. **When you add a new FFI function** (`crates/jessica-ffi/`): update
   the matching Kotlin `external fun` in
   `android/core/.../JessicaCoreNative.kt` AND the Swift seam in
   `ios/JessicaCore/Sources/JessicaCore/JessicaFFI.swift` in the same
   commit. The FFI is a three-way contract.

## 7. Voice-First Sanity Check

Before declaring any feature done, mentally simulate the **blind user
scenario**: user has no screen, no keyboard, just a mic and a speaker.
Can they:

- Wake the system?
- Discover the new feature by asking ("what can you do?")?
- Recover from a misrecognized command?
- Reach SSH-recovery mode by voice if normal voice path breaks?

If any answer is "no" without a written justification in the design doc,
the feature is incomplete.

## 8. Verification Checklist (before declaring task done)

- [ ] `make test-fast` is green.
- [ ] `make test-vm` is green for the affected scenarios.
- [ ] Updated docs match new behaviour (1:1 with `configs/` changes).
- [ ] No model weights or build artefacts staged for commit.
- [ ] Cross-agent rules (`AGENTS.md`, this file) agree.
- [ ] Any new YAML config has a default in `configs/` AND a doc entry in
      `docs/07-CONFIGURATION.md`.
- [ ] Any new voice intent has a scenario file in `rpi5/tests/scenarios/`.
- [ ] **PL + EN parity (Polish-first):** every new intent has both `pl:`
      and `en:` triggers; every new assistant phrase exists in both
      languages; every new scenario has its PL/EN counterpart (or a
      documented reason why bilingual coverage is N/A — e.g., a
      fault-injection scenario with no voice content).
- [ ] The voice-first sanity check (§7) is satisfied.
