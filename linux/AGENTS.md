# AGENTS.md — `linux/` surface

Cross-agent notes for the Linux node surface (paul now, the Pi later). Read the
root [`../AGENTS.md`](../AGENTS.md) first — it is the baseline; this only adds
surface specifics. Most-restrictive guidance wins.

## What this surface is
The **server** form of Jessica: CLI/REPL/socket, no wake word. It reuses the
portable engine (`Assistant`) and the shared `domains/` libs; only the adapters
(LLM/TTS/ASR) are node-specific and come from the **mesh** (`configs/mesh.yaml`).

## Rules specific here
- **Don't fork the engine.** Import `blazend...assistant` + `domains/`; never copy
  routing/RAG/memory logic into `linux/`. New cross-node logic goes in `domains/`.
- **Mesh for the "where."** Resolve endpoints via `mesh_registry` — no hardcoded
  peer URLs. Task→backend policy stays in `configs/llm.yaml`.
- **Strict-improvement.** The Pi appliance must stay fully functional standalone;
  nothing here may make it depend on paul.
- **Gate:** `make test-fast` (lint + Tier 0/1, both Rust workspaces + Python,
  including this surface's tests). Polish-first; secrets stay in gitignored local
  files.

## Build
`make python` installs `linux/agent` (editable) alongside the shared libs. The
`jessica` console script is the entry point (`jessica_linux.cli:main`).
