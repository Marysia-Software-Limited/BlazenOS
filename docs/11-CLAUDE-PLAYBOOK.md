# 11 — Claude playbook

How Claude Code (or any compatible LLM harness) should work on this repo.
Read `AGENTS.md` first; this file is the operational layer on top.

## When the user says "work on blazen_os"

1. **Read `docs/00-INDEX.md` and at least `01-ARCHITECTURE.md` +
   `10-ROADMAP.md`** before touching anything.
2. **Check the current milestone.** The roadmap is authoritative; do not
   skip ahead unless explicitly asked.
3. **Use `EnterPlanMode`** for anything that touches:
   - the voice pipeline (`docs/04`)
   - model selection (`docs/05`)
   - the VM image build (`scripts/build-image.sh`, `stage-blazen/`)
   - the test harness (`tests/tools/*`)
4. **Always use `TaskCreate` / `TaskUpdate`** for multi-step work.
   Mark a task in_progress before starting, completed immediately after.

## When the user says "test"

Decide by scope:

| User says           | Run                                            |
|---------------------|------------------------------------------------|
| "smoke test"        | `make test-fast` (Tier 0+1, <2 min)            |
| "test"              | `make test` (Tier 0–3, 10–30 min)              |
| "test the VM"       | `make test-vm` (Tier 2+3 only)                 |
| "scenario N"        | `make test-scenario S=<name>`                   |
| "soak"              | `make test-soak` (24h — confirm before starting)|
| "test on real Pi"   | Manual rig — guide the user; do not run unattended |

After any test, summarise: which tiers, pass/fail per scenario, latency
budget breaches, surprising failures.

## When the user says "let do maintenance"

Follow the canonical maintenance scenario from `AGENTS.md` §3:

1. Finish requested implementation.
2. `make test-fast`.
3. `make test-vm`.
4. `make audit`.
5. Surgical cleanups.
6. Re-run 2–4.
7. **Then** update docs and rules.

Do **not** edit docs or rules first.

## When you are about to invent a new intent

1. Add it to `configs/intents/system.yaml` (or a new plugin file) with
   **both** `en:` and `pl:` triggers (per
   [`docs/13-LANGUAGES.md`](13-LANGUAGES.md)).
2. Add a scenario under `tests/scenarios/` (PL counterpart too if the
   intent has spoken triggers).
3. Update `configs/voice-policy.yaml` if it mutates state.
4. Mention it in `docs/04-VOICE-PIPELINE.md` and (if it mutates state)
   `docs/07-CONFIGURATION.md`.
5. Run `make test-scenario S=<new>` before declaring done.

## When you are about to add a new component (or split an existing one)

1. Decide Python vs Rust per [`docs/14-RUST-PYTHON-SPLIT.md`](14-RUST-PYTHON-SPLIT.md) §1.
   If you can't decide in under a minute, the answer is to split it.
2. Update the component table in `docs/03-SOFTWARE-STACK.md`.
3. If a new IPC topic is involved: add the JSON Schema under
   `configs/_schema/events/<topic>.schema.json`, then run
   `make gen-events` to regenerate Python + Rust types. Never hand-edit
   the generated files.
4. Add a systemd unit under `stage-blazen/files/etc/systemd/system/`.
5. Update `blazend.target` so the new unit boots with the stack.
6. Add Tier 1 component tests (Python with mocked Rust peers, or vice
   versa).

## When you are about to use FFI inside a component

Don't. Split the component. The IPC contract is the only cross-language
seam. If you genuinely need a Python helper from a Rust binary (or
vice versa), make it its own blazend-* unit.

## When you are about to switch a model

1. Update the config file (`asr.yaml` / `llm.yaml` / `tts.yaml`).
2. Update the size, latency, and license entries in `docs/05-MODELS.md`.
3. Verify the SHA256 in the YAML matches the downloaded file.
4. Run `make audio-fixtures` and `make test-vm` — TTS voice changes
   regenerate user-input fixtures.
5. Compare latency budgets in `docs/01-ARCHITECTURE.md` and adjust
   only with a recorded decision block.

## When you are about to touch SSH or firewall config

This is in the **most restrictive** bucket per `AGENTS.md` §1.3 (SSH is
break-glass).

1. Plan first (`EnterPlanMode`).
2. Justify in the design doc (`docs/06-SSH-BOOTSTRAP.md` decision block).
3. Add a Tier 2 test that confirms SSH state matches the new policy.
4. Update `docs/07-CONFIGURATION.md`.

## When tests fail in QEMU but not on real hardware (or vice versa)

Follow `docs/09-VM-TESTING.md` §"When the VM and the real Pi disagree".
Never silently disable assertions.

## When the user reports a latency regression

1. Run `make test-vm` with `--profile` flag — produces
   `vm-runs/<ts>/perf.json`.
2. Compare against the budgets in `docs/01-ARCHITECTURE.md`.
3. Bisect against `git log` — first run `make test-vm` at HEAD~5 and
   walk forward.
4. Report numbers, not vibes.

## When you are unsure about a model choice

Spawn two `general-purpose` agents in parallel:

- Agent A: "Evaluate **<model 1>** on the 50-line commands fixture set.
  Report WER, latency, RAM."
- Agent B: same with **<model 2>**.

Then synthesise the results. Do not pick a model without numbers.

## When the user is in Auto Mode

Bias toward making reasonable calls and producing artefacts. The
exception: any of the **risky actions** listed in `CLAUDE.md` §5 still
require explicit confirmation.

## When you are about to declare a task done

Run the §8 checklist in `CLAUDE.md`. The voice-first sanity check (§7)
is the one most often forgotten.

## When the user says "Polish"

This project is being designed by a Polish-speaking maintainer.

- The README has a Polish TL;DR.
- Comments and commits stay in English so the contributor base scales.
- The Polish TTS voice (`pl_PL-darkman-medium`) and Whisper multilingual
  ASR are first-class supported languages from M6.
- If the user writes in Polish, reply in Polish but keep file content
  in English unless it's user-facing documentation that already exists
  in Polish.
