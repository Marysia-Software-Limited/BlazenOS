# 08 — Testing strategy

A voice-first, ML-driven OS has more failure surface than a typical
embedded distro. The test pyramid is therefore deeper than usual.

## Five tiers

| Tier | Name                  | Where it runs               | Wall time | What it proves |
|-----:|-----------------------|-----------------------------|----------:|-----------------|
| 0    | Unit                  | Host (pytest)               | <30 s     | Pure functions, parsers, schemas. |
| 1    | Component integration | Host (pytest + mocks)       | <2 min    | One blazend-* unit at a time with mocked IPC peers. |
| 2    | Pipeline integration  | QEMU (image boot)           | 3–10 min  | All units boot, register, talk over Unix sockets. |
| 3    | Voice scenarios       | QEMU + synthetic audio I/O  | 5–20 min  | End-to-end behaviour per scenario file. |
| 4    | Hardware-in-the-loop  | Real Pi 4/5                 | manual    | Real audio path, real mic, real speaker. |
| 5    | Soak                  | QEMU or real Pi, 24h        | 24h+      | Memory leaks, drift, model degradation. |

`make test` runs Tier 0–3. Tier 4 and 5 are gated by `make test-hw` and
`make test-soak`.

## Coverage target (2026-06-22)

Python line coverage target is **≥ 80%** (`pytest --cov=blazend`), weighted
toward **integration/e2e** rather than thin unit tests: the highest-value
suites exercise the **voice loop** (`blazend.voice.runner` + the
`voice/__main__` wiring: wake → capture → ASR → brain → TTS, plus the
`radio_play`/`radio_stop` and streamed-TTS paths) and the **unit entrypoints**
(`*/__main__.py`) with all I/O (sockets, models, audio, subprocess, network)
mocked. New this session: `blazend-player` has Rust unit tests
(`cargo test -p blazend-player`: URL detection, content-type hints, gain
clamping); the radio path is integration-tested through `StreamPlayer`
(subprocess spawn monkeypatched). The marginal WM8960 mic (Tier 4 / HIL) is a
hardware limitation, not a test gap — see [`02-HARDWARE.md`](02-HARDWARE.md).
Coverage is informational, not a hard CI gate; `make test-fast` (lint + Tier
0+1) remains the merge gate.

## Tier 0 — Unit

- **Python:** `pytest rpi5/tests/unit` — config schema validation, intent
  parsing, voice-policy evaluation, scenario YAML parser, latency
  budget checker, IPC client.
- **Rust:** `cargo test --workspace` — per-crate unit tests for IPC
  codec, ring buffer, wake threshold logic, TTS chunker, watchdog
  timer state machine.
- `make test-fast` runs both. Each tier-0 run is <60 s on a developer
  laptop.

## Tier 1 — Component integration

Each blazend-* component runs against **mocked** peers across the IPC
contract. Because peers are mocked over the JSON wire, a Python
component test does not need a Rust runtime and vice versa.

- `blazend-asr` (Python) test: feed pre-recorded WAV files, assert
  transcript equality (allowing WER ≤ 5%).
- `blazend-brain` (Python) test: feed a transcript, assert intent
  classification and reply structure (not exact text — see "Replaying
  replies" below).
- `blazend-tts` (Rust) test: `cargo test -p blazend-tts` — render a
  known sentence, assert non-empty PCM and duration within ±10%.
- `blazend-wake` (Rust) test: `cargo test -p blazend-wake` — feed WAV
  fixtures, assert detection score above threshold for positives and
  below for negatives.
- `blazend-orchestrator` (Python) test: drive synthetic IPC messages
  through a stub server, assert state transitions written to
  `/run/blazen/state.json`.

Python tests live under `rpi5/tests/component/<unit>/`. Rust tests live in
each crate's `tests/` directory (`crates/<crate>/tests/*.rs`).

## Tier 2 — Pipeline integration

`tests/pipeline/test_boot.py` boots the QEMU image and asserts:

1. SSH responds on port 2222 within 60 s.
2. `systemctl is-active blazend-*` is true for every unit.
3. `/run/blazen/state.json` reports `ready: true` within 30 s.
4. No `blazend-*` unit restarted during the first 5 min.

## Tier 3 — Voice scenarios

A **scenario** is a YAML file that describes a conversation. The runner
(`rpi5/tests/tools/e2e-runner.py`):

1. Reads the scenario.
2. Boots the QEMU image (or reuses a hot snapshot).
3. For each `user` turn:
   - Synthesises the line via Piper into a WAV (host-side).
   - Pipes it into the VM's virtual mic (see [`09-VM-TESTING.md`](09-VM-TESTING.md)).
4. For each `assistant` turn:
   - Captures the VM's virtual speaker.
   - Runs ASR (host-side) and asserts on transcript per the rules below.
5. Cleans up the snapshot.

### Scenario YAML schema

```yaml
id: 04-set-volume
language: en              # en | pl | mixed
synth_voice: en_US-amy-medium   # Piper voice used to render user turns
description: User asks to set volume to 70 percent.
preconditions:
  audio.volume: 50
turns:
  - user: "hey Jessica"
    expect: wake_acknowledged
  - user: "set the volume to seventy percent"
    expect:
      assistant_matches_any:
        - "volume is now 70 percent"
        - "ok, volume set to 70 percent"
      audio.volume: 70
      assistant_language: en
  - user: "thanks"
    expect: assistant_polite_response
postconditions:
  no_unit_restarts: true
  latency_budget:
    wake_to_first_tts_ms_p95: 1800   # Pi-5-equivalent QEMU profile
```

The `language:` field tells the runner which Piper voice to use when
synthesising user audio (PL turns are rendered with a Polish voice so
the mic sees realistic Polish phonetics, not Anglo-Polish). Turn-level
`synth_voice:` overrides the scenario default — used by `09-language-switch`
to mix EN and PL within the same scenario.

Per-turn `assistant_language: en|pl` asserts the assistant replied in
the expected language (the runner classifies the captured TTS through
Whisper's language detector).

### Replaying replies — how to assert on a stochastic LLM?

Three matchers:

1. **`assistant_matches_any`** — list of acceptable utterances.
2. **`assistant_intent_matches`** — semantic match against an intent
   schema (e.g., `intent: greeting`).
3. **`assistant_semantic_similarity`** — cosine similarity of
   sentence embeddings (`>= 0.78` default) against a reference reply.

For early iterations we lean on `assistant_intent_matches` because we
don't want to overfit to a specific LLM model.

### State assertions

`expect.audio.volume`, `expect.system.power.shutting_down`,
`expect.wake_word.name` and similar are read from `/run/blazen/state.json`
via SSH after the turn settles.

## Tier 4 — Hardware-in-the-loop

Run on a real Pi 4 or Pi 5. The harness:

1. Plays scenarios from a small Bluetooth speaker placed near the Pi's
   mic.
2. Records the Pi's speaker via a USB mic placed near it.
3. Otherwise the same scenario format as Tier 3.

This catches issues invisible in QEMU: AEC tuning, beamforming, gain
staging, thermal throttling, SD-card stuttering.

`make test-hw` is the entrypoint, but it's manual: requires physical
setup.

## Tier 5 — Soak

`make test-soak` runs a randomised loop of scenarios for 24 hours:

- Reboot every 4 hours; assert clean re-init.
- Random utterances drawn from a 500-line pool, weighted by intent.
- Per-hour: RSS of each blazend-* unit, p95 latency, ASR WER, intent
  accuracy.
- Fail if RSS grows >20% over 24 h or if p95 latency drifts >25%.

## CI matrix

| Job                | When            | Tier | Runs on            |
|--------------------|-----------------|-----:|--------------------|
| `lint`             | every PR        | 0    | GitHub Actions     |
| `unit`             | every PR        | 0    | GH Actions         |
| `component`        | every PR        | 1    | GH Actions (mocks) |
| `pipeline`         | every PR        | 2    | self-hosted (QEMU) |
| `scenarios-fast`   | every PR        | 3    | self-hosted (QEMU) |
| `scenarios-full`   | nightly         | 3    | self-hosted        |
| `hardware`         | manual / weekly | 4    | physical Pi rig    |
| `soak`             | weekly          | 5    | self-hosted        |

QEMU runs are tagged with `BLAZEN_QEMU_MACHINE=raspi5b` for the
reference profile and `raspi4b` once a week for the supported profile.

## Bilingual coverage requirements

For PL + EN parity (see [`13-LANGUAGES.md`](13-LANGUAGES.md)):

- Every fast-path intent test must run in both languages — either as
  separate scenarios or as a parametrised one.
- Latency budgets for PL scenarios are 10–15% looser than EN to account
  for slightly higher ASR time and TTS voice prosody.
- `rpi5/tests/unit/test_bilingual_coverage.py` is the gate that fails CI
  when an intent ships EN-only triggers, or when there are <3 PL
  scenarios, or when the language-switch scenario is missing.

## What we do **not** test

- Exact LLM token sequences. We test intents and semantic similarity.
- Audio bit-exact equality. We test transcription of TTS output.
- Network connectivity. blazen is offline by design.
- Translation quality. We test that the assistant *picks* the right
  language; we do not score the quality of translation between EN and PL.
