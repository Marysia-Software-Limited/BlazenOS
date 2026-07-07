# CLAUDE.md — `linux/` surface

Claude-only notes for the Linux node surface. Read the root
[`../CLAUDE.md`](../CLAUDE.md) and [`AGENTS.md`](AGENTS.md) first; this adds
Claude specifics. Prefer the most restrictive guidance.

## Orientation
- Surface purpose + layout: [`README.md`](README.md).
- The agent imports the portable engine from the `blazen_os` package
  (`rpi5/src/blazend`) — installed editable by `make python`. Read
  `rpi5/.../assistant/engine.py` (the `Assistant`) and `core/model_router.py`
  before changing wiring.
- Mesh contract: [`../macos/docs/03-LLM-MESH.md`](../macos/docs/03-LLM-MESH.md);
  loader API in [`../domains/mesh-registry/`](../domains/mesh-registry/).

## Working here
- Keep diffs surgical; reuse over rewrite. If you need shared logic, put it in
  `domains/`, not `linux/`.
- Run `make test-fast` before declaring done. Live-check the LLM path against
  paul's Ollama (`BLAZEN_NODE=paul jessica "co potrafisz?"`), and TTS against the
  `blazen-xtts` service — but keep unit tests network-free (inject fakes, see
  `agent/tests/test_node.py`).
- Don't commit secrets/models. Don't break the Pi or rachel.
