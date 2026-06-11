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

Whenever the user says "rules changed" or you modify a doc, refresh these.

## 2. Project Snapshot

- **One-line:** voice-first Linux OS for Raspberry Pi 5 — **16 GB is the
  reference platform**, 8 GB a supported secondary target (with optional
  Hailo accelerator for the LLM), fully on-device ML, SSH only as a
  service-recovery back door. See [`docs/02-HARDWARE.md`](docs/02-HARDWARE.md).
- **Phase:** **M0 scaffolding** — the repo currently contains design docs,
  configs, scripts, test harness skeletons. No bootable image yet.
- **Monorepo layout (2026-06-11):** the repo root holds the **shared core**
  common to all three platforms — `crates/` (Rust: `blazend-ipc`,
  `blazend-fabric`, `jessica-core`, `jessica-ffi`), `configs/` (shared
  contract + appliance config), `docs/`, `scripts/`. The **Raspberry Pi 5
  appliance** is a self-contained project under **`rpi5/`** (Python
  `rpi5/src/blazend`, appliance crates `rpi5/crates/*`, `rpi5/stage-blazen`,
  `rpi5/tests`). The `ios`/`android` sibling repos consume `crates/` +
  `configs/`. Full tree in [`docs/14-RUST-PYTHON-SPLIT.md`](docs/14-RUST-PYTHON-SPLIT.md) §4.
- **Five things that must always be true:**
  1. The system is usable with **zero peripherals beyond a USB mic + speaker**.
     If a change forces a keyboard/monitor for daily use, reject it.
  2. ML inference runs **on-device**. No outbound calls to cloud LLMs/ASRs
     during normal operation. (Telemetry off by default; opt-in only.)
  3. SSH is **break-glass** — guarded by failure of the voice path, not a
     replacement for it.
  4. The **CPU path is the contract**. Any feature relying on the optional
     Hailo accelerator must degrade gracefully to CPU. The accelerator is
     a strict-improvement path, never a precondition. See
     [`docs/12-ML-ACCELERATOR.md`](docs/12-ML-ACCELERATOR.md).
  5. **EN and PL are co-equal.** Every user-facing voice surface — wake
     word, intents, confirmations, replies, error tones, system messages —
     ships in both languages. A change that lands an English-only
     intent / phrase / scenario is incomplete until the Polish
     counterpart exists. See [`docs/13-LANGUAGES.md`](docs/13-LANGUAGES.md).
  6. **Python and Rust are the two implementation languages.** Hot loops,
     audio I/O, wake word, TTS, watchdog and the IPC wire are **Rust**.
     Orchestrator, ASR, LLM, bootstrap and config are **Python**. No
     third language enters the core stack. Cross-language calls go
     through the IPC contract, never through FFI inside a component.
     See [`docs/14-RUST-PYTHON-SPLIT.md`](docs/14-RUST-PYTHON-SPLIT.md).

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
6. **Dev rig split.** Linux (`paul`) is the **primary rig for blazen_os**
   from now on; macOS is for `rachel` (the mobile twin) development.
   See [`docs/15-DEV-WORKFLOW.md`](docs/15-DEV-WORKFLOW.md).

   **If you (Claude) are running on `paul`:** this is your home repo.
   You handle the full blazen_os surface — image builds, cross-compile,
   Tier 2-3 tests, hardware bring-up. Mobile spec changes (under
   `docs/product/`) come from the macOS session via git or rsync;
   coordinate before editing them.

   **If you (Claude) are running on macOS:** treat this repo as
   **read-mostly**. You can edit `docs/product/` (the shared
   cross-implementation spec, also consumed by `../rachel/`) but you
   should not start image builds or run paul-only tasks. Sync with
   `make sync-paul` after touching shared docs.
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
7. **Language choice for new code:** check
   [`docs/14-RUST-PYTHON-SPLIT.md`](docs/14-RUST-PYTHON-SPLIT.md) §1
   before adding a new component. If a Rust component needs to do
   "just one Python thing", that's a sign to expose it as a separate
   blazend-* unit and let the IPC contract carry the call.
8. **When you add a new IPC event:** add the JSON Schema under
   `configs/_schema/events/` and regenerate types in both languages via
   `make gen-events`. The schema is the source of truth, not the
   hand-written Python or Rust type.

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
- [ ] **EN + PL parity:** every new intent has both `en:` and `pl:`
      triggers; every new assistant phrase exists in both languages;
      every new scenario has a PL counterpart (or a documented reason
      why bilingual coverage is N/A — e.g., a fault-injection scenario
      with no voice content).
- [ ] The voice-first sanity check (§7) is satisfied.
