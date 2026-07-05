# macOS surface — **rachel** (Jessica on Apple Silicon)

`rachel` is the **macOS node** of the Jessica constellation: a full Jessica agent
that runs on the maintainer's MacBook, uses **Apple-Silicon AI acceleration**
(MLX / Metal / Apple Speech) for on-device ML, and **joins the shared LLM mesh**
with the other nodes so all three share models *and* conversation context.

> **The constellation**
> | Node | Machine | Role | Local AI |
> |------|---------|------|----------|
> | **jessica** | Raspberry Pi 5 | the always-on voice **appliance** | Bielik 1.5B/4.5B (CPU; Hailo/Orin later) |
> | **rachel**  | MacBook (host `rebeca`*) | interactive Jessica on the desktop | **MLX / Metal** LLM on the Neural Engine/GPU |
> | **paul**    | Linux + RTX 3090 | the heavy-lift GPU + build rig | Ollama Bielik 11B, Whisper, cloud keys |
>
> \* *Naming to confirm: the message named the macOS node **rachel** but said the
> Claude session runs on **rebeca**. This scaffold treats `rachel` = the node/agent
> name and `rebeca` = the MacBook hostname. Correct in `docs/00-CONTEXT.md` if wrong.*

## What rachel is (and isn't)
- **Is:** a peer node like the Pi appliance — a Jessica agent you talk to on your
  Mac, with its own fast on-device LLM (MLX), that contributes its model to the
  mesh and shares memory/context with `jessica` and `paul`.
- **Is not:** the iOS phone app (see [`../ios/`](../ios/)) — that's a separate
  personal-assistant surface using Apple's OS ML. rachel is closer in role to
  [`../rpi5/`](../rpi5/) (a full host) than to `ios/`.

## The two things this node adds
1. **Apple-Silicon LLM** — run Bielik / Qwen / Llama via **MLX** (or llama.cpp
   Metal), exposed as an OpenAI-compatible endpoint so it plugs into the existing
   `ModelRouter` as just another backend.
2. **Distributed LLM mesh via DSPy** — the DSPy-compiled programs
   ([`../configs/prompts/`](../configs/prompts/)) run against the **best backend in
   the whole pool** (rachel's MLX, jessica's Bielik, paul's Ollama 11B, cloud
   GPT-5.5) — Ollama becomes one backend of several, not the only one. Context is
   shared across nodes via [`../domains/blazend-fabric`](../domains/blazend-fabric/).

## Shared core (do NOT reimplement)
rachel reuses the same Rust mind every surface shares:
[`../domains/jessica-core`](../domains/jessica-core/) (intents/memory/routing types),
[`../domains/jessica-ffi`](../domains/jessica-ffi/) (C ABI for Swift),
[`../domains/blazend-fabric`](../domains/blazend-fabric/) (cross-node sync). It reuses
the appliance's `ModelRouter` design and the DSPy prompt-compile pipeline verbatim.

## Read next
1. [`docs/00-CONTEXT.md`](docs/00-CONTEXT.md) — the vision + constellation + naming (read first).
2. [`docs/01-ARCHITECTURE.md`](docs/01-ARCHITECTURE.md) — the rachel stack.
3. [`docs/03-LLM-MESH.md`](docs/03-LLM-MESH.md) — the distributed DSPy LLM mesh (the crux).
4. [`docs/02-BUILD-INSTRUCTIONS.md`](docs/02-BUILD-INSTRUCTIONS.md) — step-by-step for the build session.
5. [`AGENTS.md`](AGENTS.md) + [`CLAUDE.md`](CLAUDE.md) — agent working rules for this surface.

**Status:** scaffold only — no code yet. Prepared by the `jessica` (Pi) Claude
session for a fresh Claude session on the Mac to implement.
