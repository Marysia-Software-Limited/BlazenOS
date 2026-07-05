# CLAUDE.md — macOS surface (rachel)

Claude-only operational notes for `macos/`. Read [`AGENTS.md`](AGENTS.md) (the
cross-agent baseline) and the repo root [`../CLAUDE.md`](../CLAUDE.md) first; this
adds Claude specifics. Prefer the **most restrictive** guidance when they differ.

## Orientation (do this first)
1. Read, in order: [`docs/00-CONTEXT.md`](docs/00-CONTEXT.md),
   [`docs/01-ARCHITECTURE.md`](docs/01-ARCHITECTURE.md),
   [`docs/03-LLM-MESH.md`](docs/03-LLM-MESH.md),
   [`docs/02-BUILD-INSTRUCTIONS.md`](docs/02-BUILD-INSTRUCTIONS.md).
2. **Confirm naming with the user** (rachel = node, rebeca = host — verify) before
   creating files or hostnames.
3. Confirm the host-language choice (Python-agent A vs native-Swift B).

## Build approach
- Follow the phases in `docs/02-BUILD-INSTRUCTIONS.md`. **Phase 1 first** — stand up
  rachel's MLX OpenAI-compatible LLM server and register it in the Pi's router; that
  proves the mesh end-to-end before any UI. Small, testable steps.
- Use `EnterPlanMode` before non-trivial multi-file work (the mesh router changes,
  context sync, the app skeleton).
- Track multi-step work with `TaskCreate`/`TaskUpdate`.

## Operational notes
- **Testing MLX locally:** `curl` the OpenAI endpoint for a Polish reply + tokens/s
  before wiring the mesh. Verify Bielik/Qwen Polish quality.
- **Mesh changes are shared** with the `jessica` (Pi) session — `../configs/llm.yaml`,
  `../rpi5/.../model_router.py`, `../domains/blazend-fabric`. Land them in small
  commits on the shared branch and note them so both ends stay consistent.
- **Secrets:** cloud keys (Azure/OpenAI) live in a local, gitignored file — never in
  git, never pasted into chat. The user sets them.
- **Don't** break `jessica`: nothing here may make the Pi appliance depend on rachel.
- **iOS xcodebuild / signing** is a separate concern (`../ios/`); rachel is a
  macOS host agent, not an App Store target.

## Verification before "done"
- MLX server returns quality Polish; Pi routes a `recommend` to rachel and back
  (logs show `engine=rachel-mlx`), with clean fallback when the Mac is off.
- Context: a note saved on one node is recalled on another.
- Root invariants intact (Polish-first, on-device default, cloud opt-in, PL+EN
  parity, no models/secrets committed).
