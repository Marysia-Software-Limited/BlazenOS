# src/

The Python source for the `blazend-*` systemd units lives here, organised
one package per unit:

```
src/
├── blazend/
│   ├── orchestrator/
│   ├── audio_in/
│   ├── wake/
│   ├── asr/
│   ├── brain/                # contains the engine selector (CPU vs Hailo)
│   ├── tts/
│   ├── audio_out/
│   ├── health/
│   └── config/
└── stage_blazen/             # pi-gen "stage-blazen" assets (systemd units, conf snippets)
```

Empty in M0. Filled in M1+ following [`../docs/10-ROADMAP.md`](../docs/10-ROADMAP.md).
