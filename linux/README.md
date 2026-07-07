# `linux/` — Jessica on Linux nodes (paul now, the Pi later)

A **Linux node surface** in the blazen_os constellation. Where `rpi5/` is the
always-on **voice appliance** and `macos/` (rachel) is the **desktop** agent,
`linux/` is the **server** form of Jessica: reached over a CLI/REPL/socket (no
wake word), reusing the portable engine and wiring its adapters to whatever the
node advertises on the **mesh**.

Today it targets **paul** (Linux + RTX 3090): the LLM is paul's GPU **Ollama-11b**
resolved from [`../configs/mesh.yaml`](../configs/mesh.yaml), TTS is paul's
**XTTS-v2** service, ASR is the remote **Whisper**. The same surface is intended
to run on the Pi as a Linux box later.

## Layout
- `agent/` — the Python agent (`jessica-linux`): `node.py` wires the mesh →
  `ModelRouter`/`Assistant`; `cli.py` is the `jessica` front door. Tests under
  `agent/tests/`.

## The reuse rule
The conversational **engine is not forked** — `agent/` imports the portable
`Assistant` (memory, routing, RAG, prompts) from the `blazen_os` package
(`rpi5/src/blazend`, editable-installed by `make python`) and the shared
`domains/` libs (`mesh-registry`, `audiobook-catalog`). Only the **adapters**
(which LLM/TTS/ASR) are node-specific, and those come from the mesh. When the mind
migrates to `domains/` (Phase 4), the import path moves; the surface does not.

## Run
```sh
make python                              # installs the agent + shared libs
BLAZEN_NODE=paul jessica "co potrafisz?" # one-shot, answers in Polish via Ollama-11b
BLAZEN_NODE=paul jessica                 # interactive REPL
BLAZEN_NODE=paul scripts/mesh-check.py   # (repo root) verify mesh membership + reachability
```

## Rules
Root [`../AGENTS.md`](../AGENTS.md) + [`../CLAUDE.md`](../CLAUDE.md) are the
baseline (Polish-first, on-device by default, mesh is strict-improvement — never
break the Pi standalone, `domains/` for common code). `make test-fast` is the gate.
Mesh design: [`../macos/docs/03-LLM-MESH.md`](../macos/docs/03-LLM-MESH.md);
domain layout: [`../docs/19-DOMAIN-ARCHITECTURE.md`](../docs/19-DOMAIN-ARCHITECTURE.md).
