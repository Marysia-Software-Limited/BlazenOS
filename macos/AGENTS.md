# AGENTS.md — macOS surface (rachel)

Cross-agent baseline for any LLM harness working in `macos/`. Read the **repo
root** [`../AGENTS.md`](../AGENTS.md) first — it carries the hard invariants that
apply everywhere. This file adds macOS-node specifics. When root and this file
disagree, prefer the **more restrictive** rule unless the user overrides.

## What this surface is
`macos/` is the **rachel** node: a full Jessica agent on Apple Silicon that uses
on-device ML (MLX/Metal/Apple Speech) and joins the shared LLM mesh + context with
`jessica` (Pi) and `paul` (Linux). See [`docs/00-CONTEXT.md`](docs/00-CONTEXT.md).
It is **not** the iOS phone app (`../ios/`).

## Read order
1. [`../AGENTS.md`](../AGENTS.md), [`../CLAUDE.md`](../CLAUDE.md) (root invariants).
2. [`docs/00-CONTEXT.md`](docs/00-CONTEXT.md) → `01-ARCHITECTURE.md` → `03-LLM-MESH.md` → `02-BUILD-INSTRUCTIONS.md`.
3. [`../docs/17-MOBILE-MONOREPO.md`](../docs/17-MOBILE-MONOREPO.md) (shared core + contract).

## Invariants that still hold here
- **Polish-first at runtime; keep PL + EN assets in parity** (intents, phrases).
- **On-device by default.** rachel's LLM/ASR/TTS run on Apple Silicon. Cloud
  (Azure TTS, GPT-5.5) is **opt-in per feature**, key in a local secrets file.
- **No node is a hard dependency.** `jessica` must stay fully functional if rachel
  is off. The mesh is strict-improvement, like the accelerator path.
- **Reuse the shared core.** Bind/call `../domains/jessica-core` (+ `jessica-ffi`)
  and the appliance's `ModelRouter` + DSPy prompts — don't reimplement them.
- **DSPy is compiled OFFLINE on paul**, shipped as static `../configs/prompts/*.json`;
  runtime fills them (no dspy needed to *run*).

## Working rules
- Shared files (`../configs/llm.yaml`, `model_router.py`, `../domains/blazend-fabric`)
  are co-owned with the `jessica` session — coordinate changes, keep the router
  schema consistent on both ends.
- Never commit models, weights, or secrets. Cloud keys → a gitignored local file.
- Match the surrounding code's style/idiom. Minimal, surgical diffs.
- Prefer the Python-agent path (A) for fastest reuse; native Swift (B) later.
