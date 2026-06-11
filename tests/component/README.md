# Tier 1 — component integration

One blazend-* unit at a time, with mocked peers. Populated as units land
(M2 wake → M3 asr → M4 brain/tts).

Per-unit fixtures and mocks live next to the tests:

```
component/
├── wake/
│   ├── conftest.py
│   ├── test_wake_threshold.py
│   └── fixtures/*.wav
├── asr/
│   ├── conftest.py
│   ├── test_transcripts.py
│   └── fixtures/*.wav
├── brain/
│   ├── conftest.py
│   └── test_intent_routing.py
├── tts/
│   ├── conftest.py
│   └── test_synthesis.py
└── orchestrator/
    ├── conftest.py
    └── test_state_machine.py
```

Each `conftest.py` mocks the IPC peers (Unix sockets) so the unit under
test sees realistic message shapes without other components actually
running.

See [`../../docs/08-TESTING.md`](../../docs/08-TESTING.md) §"Tier 1".
