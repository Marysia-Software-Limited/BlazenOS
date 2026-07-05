# 03 — The shared LLM mesh (DSPy across jessica · rachel · paul)

The headline of the rachel work: **stop treating one Ollama box as "the LLM."**
Instead, every node advertises its model(s), and **DSPy programs run against the
best backend in the whole pool**. Ollama becomes *one* backend among several
("instead of ollama **or with** ollama").

## Where we are today (single-node)

`ModelRouter` ([`../rpi5/…/core/model_router.py`](../rpi5/src/blazend/domains/ai_orchestrator/core/model_router.py))
already does task→backend selection with graceful fallback, driven by
[`../configs/llm.yaml`](../configs/llm.yaml) `routing:`:

```
command   → [ollama-11b, bielik-1.5b]
recommend → [ollama-11b, bielik-4.5b]
open_qa   → [gpt-5.5, ollama-11b, bielik-4.5b]
```

Backends satisfy one tiny protocol — `available` / `chat` / `chat_stream`. Ollama
+ OpenAI are network clients; Bielik is local llama.cpp. The DSPy prompts are
compiled offline on paul and shipped as static JSON
([`../configs/prompts/`](../configs/prompts/)); the runtime just fills them.

**So the mesh is a small generalisation, not a rewrite:** add per-node network
backends + a node-aware ordering.

## Target: multi-node mesh

### 1. Backend registry (data, not code)
Extend `routing.backends` (or a new `configs/mesh.yaml`) so each backend names its
**node + endpoint + model**:

```yaml
mesh:
  self: rachel                      # this node's name (per host)
  nodes:
    jessica: { url: "http://10.55.0.1:PORT",      kinds: [bielik-1.5b, bielik-4.5b] }
    paul:    { url: "http://192.168.50.102:11434", kinds: [ollama-11b] }        # Ollama
    rachel:  { url: "http://127.0.0.1:8080",       kinds: [mlx-bielik-4.5b, mlx-qwen-8b] }
  # per-task preference is LOCALITY-aware: prefer this node's fast model, then peers,
  # then cloud. "nearest capable wins", with the existing availability fallback.
  tasks:
    command:   [self-fast, jessica-1.5b, paul-11b]
    recommend: [self-4.5b, paul-11b, jessica-4.5b]
    open_qa:   [gpt-5.5, paul-11b, self-4.5b]
```

Each entry resolves to an **OpenAI/Ollama-compatible HTTP client** — the same
`Llm` protocol the router already uses. `available` = a cached reachability probe
(reuse the 3 s Ollama probe pattern). So `rachel-mlx`, `jessica-served`, and
`paul-ollama` are all just clients with URLs.

### 2. Each node SERVES its model
- **rachel:** `mlx_lm.server` (or `llama-server` Metal) → OpenAI API on a port.
- **paul:** already serves Ollama `:11434` (OpenAI-compatible).
- **jessica:** today Bielik is in-process (llama.cpp). To share it *out*, wrap it
  in a tiny OpenAI-compatible HTTP shim (or run `llama-server`). Optional — the Pi
  is the weakest node, so it's mostly a *consumer* of the mesh, not a provider.

### 3. DSPy is the program layer
- Signatures + compiled few-shot demos are **shared artifacts** in
  `configs/prompts/` — every node runs the identical program.
- Compilation stays **offline on paul** (`scripts/compile-prompts.py`); the mesh
  changes *which endpoint* executes a compiled program, not the program.
- Optionally use `dspy.LM(...)` per-backend so a DSPy module can be pinned to a
  node (e.g. a heavy `recommend` reasoning step → paul's 11B) via `dspy.context`.
  Keep the **router (rules) for transport/selection**; use DSPy for the *reasoning
  structure*, exactly as decided for the appliance.

### 4. Locality + fallback policy
- **Nearest capable wins:** the node you're interacting with prefers its own fast
  model for snappy turns; escalates to paul's 11B for heavy reasoning; cloud only
  for `open_qa` (and only if a key is present).
- **Graceful degradation:** a peer that's offline is skipped (cached probe) — same
  as today's Ollama-down → local fallback. No node is a hard dependency.

## Context, not just models
A shared model pool is half the ask; the other half is **shared context** so any
node answers *as the same Jessica*. That rides `blazend-fabric` (see
[`01-ARCHITECTURE.md`](01-ARCHITECTURE.md) §Context): memory/notes/reminders/
profile/progress sync over the LAN. The mesh routes *compute*; the fabric syncs
*state*. Together they make "shared resources and context with all nodes" real.

## Build order for the mesh (smallest → whole)
1. **rachel serves MLX** on a port; verify with `curl` (OpenAI `/v1/chat/completions`).
2. **Add `rachel-mlx` to jessica's router** (`configs/llm.yaml`) as a new backend
   URL → confirm the Pi can offload a `recommend` turn to the Mac (log `engine=`).
3. **Generalise the router** to the node-aware `mesh:` schema above (self-first
   ordering, per-node probes). Unit-test ordering + fallback with fakes (mirror
   `../rpi5/tests/unit/test_model_router.py`).
4. **Context sync v1** over `blazend-fabric` (read-mostly snapshot), then deltas.
5. Compile any rachel-specific prompts **on paul**; ship to `configs/prompts/`.

## Definition of done (mesh MVP)
- From the Pi: a `recommend` request runs on **rachel's MLX** when the Mac is on,
  and falls back to paul's 11B / local Bielik when it's off — proven in logs.
- From rachel: `open_qa` → GPT-5.5, `recommend` → local MLX, heavy → paul.
- A note saved on jessica is visible to rachel (context sync).
