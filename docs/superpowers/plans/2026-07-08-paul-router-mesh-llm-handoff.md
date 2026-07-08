# Handoff — let the Pi/paul router route TO rachel's MLX LLM

> **FOR THE `paul` / `jessica` (Pi) CLAUDE SESSION.** Written by the `rachel`
> (macOS, M3 Max) session, which stood up rachel as a full mesh member (shared
> audiobooks, fabric context, and now an MLX LLM). Everything below is on `main`.
> `git pull` first.

## What rachel now serves (done, live)

rachel advertises an **`llm`** resource in [`configs/mesh.yaml`](../../../configs/mesh.yaml):

```yaml
rachel:
  resources:
    llm:
      mlx-bielik-11b: { kind: openai, url: "http://192.168.50.186:11435", model: "speakleash/Bielik-11B-v3.0-Instruct-MLX-8bit" }
```

- **OpenAI-compatible** (`POST /v1/chat/completions`), served by `mlx_lm.server`
  under `org.blazen.mlx.plist` (KeepAlive). Verified: fluent Polish, ~19 tok/s warm.
- Port **11435** (11434 is the Mac's own Ollama).
- A **deep tier** is **live** on **:11436** — `mlx-qwen72b` (Qwen2.5-72B-4bit,
  67.92 on Open PL LLM, ~5.5 tok/s warm, submit-and-wait). Already in `mesh.yaml`
  as a second `llm` resource; your router change handles both with one branch.
  Model rationale: [`macos/docs/03-LLM-MESH.md`](../../../macos/docs/03-LLM-MESH.md)
  "Which models rachel serves".

## The gap (your job)

The router builds backends by **hardcoded name** — see
[`rpi5/…/core/model_router.py`](../../../rpi5/src/blazend/domains/ai_orchestrator/core/model_router.py)
`_build()`: it knows `ollama-11b`, `gpt-5.5`, and the local llama.cpp set. Any other
name (e.g. `mlx-bielik-11b`) hits `else → "unknown backend" → dropped`. So a
mesh-advertised OpenAI LLM on a peer is invisible to routing today. `_mesh_url()`
already resolves a backend name → mesh URL (that's how `ollama-11b` gets paul's
address); we just need `_build` to construct a client for a generic `openai`-kind
mesh resource.

## Steps

1. **Generalise `_build`** — add a branch: if the backend isn't one of the known
   names but the mesh has an `llm` resource with that name and `kind: openai`,
   build an OpenAI-style client from `res.url` (+ `res.attrs['model']`). The
   existing `OpenAiClient` (used for `gpt-5.5`) or `OllamaLlm(url=...)` is the
   nearest adapter — both speak `/v1/chat/completions`; point it at
   `{url}/v1` with the resource's `model`. Keep `available` a cheap probe
   (like the `ollama-11b` TTL cache) so an offline rachel just drops out.
2. **Wire `configs/llm.yaml`** — add to `routing.backends`:
   `mlx-bielik-11b: { max_tokens: 512 }` (and `mlx-qwen72b` when it lands), then
   put rachel **ahead of the cloud, around paul** in the locality-aware order.
   Suggested (a peer's big model is a great `recommend`/`open_qa` target, on-device):
   ```yaml
   recommend: [mlx-qwen72b, ollama-11b, mlx-bielik-11b, bielik-4.5b]
   open_qa:   [mlx-qwen72b, gpt-5.5, ollama-11b, bielik-4.5b]
   command:   [ollama-11b, mlx-bielik-11b, bielik-1.5b]
   ```
   (Locality note: for a Pi turn, rachel is a peer — keep paul's `ollama-11b`
   competitive for latency-sensitive `command`; lead with rachel's big model only
   where quality matters more than ms.)
3. **Unit-test** ordering + fallback with an injected mesh + a fake OpenAI backend
   (mirror `rpi5/tests/unit/test_model_router.py`); assert `mlx-bielik-11b`
   resolves when the mesh lists it and is skipped when its probe fails.
4. **Cross-node DoD:** from the Pi, a `recommend` runs on **rachel's MLX** when the
   Mac is on (log the chosen `engine=`), and cleanly falls back to `ollama-11b` /
   local Bielik when rachel is off. `curl http://192.168.50.186:11435/v1/chat/completions`
   from the Pi to sanity-check reachability first.

## Also (pre-existing, your territory)
- `rpi5/tests/unit/test_model_router.py` already fails on a clean tree with
  `KeyError: 'bielik-1.5b-v3-instruct-q4_k_m'` (LLM-config area) — unrelated to
  rachel, flagged during the rachel work. Worth fixing while you're in this file.

## Invariants
- **Don't break the Pi.** rachel being off must never be fatal — strict-improvement.
- Keep the change in the router + `llm.yaml`; the mesh entry (the "where") is done.
- `make test-fast` is the gate (runs on Linux where `blazend` imports resolve —
  rachel couldn't run the rpi5 suite, which is why this is your half).
