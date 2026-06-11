# tests/

Test code for blazen_os. See [`../docs/08-TESTING.md`](../docs/08-TESTING.md)
for the strategy. Layout:

```
tests/
├── README.md                # this file
├── unit/                    # Tier 0 — pure-Python tests (pytest)
├── component/               # Tier 1 — single blazend-* with mocked peers
├── pipeline/                # Tier 2 — pipeline-level over QEMU
├── scenarios/               # Tier 3 — YAML voice scenarios
├── fixtures/                # generated WAVs + captured audio (gitignored)
│   └── audio/.gitkeep
└── tools/
    ├── e2e-runner.py        # scenario runner (host-side orchestrator)
    ├── synth-audio.py       # render user turns to WAV via Piper
    ├── audio_backend.py     # PipeWire / PortAudio-file abstraction
    └── voice-sim.py         # repl for poking at the pipeline by hand
```

## Quick reference

| Goal                          | Command                                 |
|-------------------------------|------------------------------------------|
| Run Tier 0 + 1                | `make test-fast`                         |
| Run Tier 0..3                 | `make test`                              |
| Run one scenario              | `make test-scenario S=01-wake-word`      |
| Regenerate user-input audio   | `make audio-fixtures`                    |
| Soak (24h)                    | `make test-soak`                         |

## Scenario YAML — schema cheat sheet

```yaml
id: NN-description
description: One line of what this scenario proves.
preconditions:
  audio.volume: 50
turns:
  - user: "hey blazen"
    expect: wake_acknowledged
  - user: "what time is it"
    expect:
      assistant_intent_matches:
        intent: clock.time
postconditions:
  no_unit_restarts: true
  latency_budget:
    wake_to_first_tts_ms_p95: 1500
```

See `tests/scenarios/*.yaml` for working examples.
