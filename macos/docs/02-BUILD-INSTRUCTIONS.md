# 02 — Build instructions (macOS / rachel session)

Step-by-step for the Claude session on the MacBook. Do the phases in order; each
ends in something testable. **Confirm the naming (rachel/rebeca) with the user
before Phase 1.**

## Prerequisites
- Apple Silicon Mac, macOS recent, Xcode + command-line tools.
- Homebrew. `git clone` this monorepo (or it's already checked out).
- Python 3.11+ (a `.venv`), Rust toolchain (for `jessica-core`/`jessica-ffi` if you
  go native), `uv`/`pip`.
- Network reachability to `jessica` (Pi) and `paul` (Linux) on the LAN.
- **Read** the repo root [`../AGENTS.md`](../AGENTS.md) + [`../CLAUDE.md`](../CLAUDE.md)
  and [`00-CONTEXT.md`](00-CONTEXT.md) first.

## Phase 0 — decide + scaffold (day 1)
1. Confirm with the user: node name (**rachel**) vs host (**rebeca**); host-language
   choice — **A) Python agent (recommended)** or B) native Swift (see
   [`01-ARCHITECTURE.md`](01-ARCHITECTURE.md) §Decision).
2. Create `macos/` app skeleton for the chosen path:
   - **A:** `macos/agent/` (Python) + `macos/app/` (thin Swift menu-bar shell, optional).
   - **B:** `macos/JessicaMac/` Xcode/SwiftPM project (mirror `../ios/project.yml`),
     linking `libjessica_ffi` (build from `../domains/jessica-ffi`).
3. Add `macos/Makefile` (mirror per-surface Makefiles) with `run`, `serve-llm`, `test`.

## Phase 1 — rachel serves an Apple-Silicon LLM (the first real win)
Goal: an OpenAI-compatible LLM endpoint on the Mac that the Pi can call.
1. Install MLX: `pip install mlx-lm` (or build `llama.cpp` with Metal).
2. Get a model that matches the mesh: **Bielik** (Qwen2.5-based → MLX-convertible)
   or a Qwen2.5-4B/7B / Llama-3.x in MLX format. Convert with `mlx_lm.convert` if
   needed (4-bit for memory).
3. Serve: `mlx_lm.server --model <path> --port 8080` → verify:
   `curl 127.0.0.1:8080/v1/chat/completions -d '{"model":"...","messages":[{"role":"user","content":"Cześć"}]}'`
   Confirm **Polish** output quality + tokens/s (should beat the Pi's 9.6/2.2).
4. **Join the mesh (minimal):** on `jessica`, add a `rachel-mlx` backend URL to
   [`../configs/llm.yaml`](../configs/llm.yaml) `routing.backends` and put it first
   for `recommend`. Verify from the Pi that a recommendation runs on the Mac
   (`journalctl -u blazend-brain` shows `engine=rachel-mlx`). This proves the mesh
   end-to-end before any UI.

## Phase 2 — the rachel agent (talk to Jessica on the Mac)
Path A (Python):
1. Reuse the appliance agent: import `ModelRouter`, `prompts.py`, `tools.py`, the
   RAG from `../rpi5/src/blazend/...` (they're plain Python). Point the router at
   the local MLX endpoint + peer nodes.
2. ASR: **Apple Speech** (`SFSpeechRecognizer`) via a small Swift/PyObjC bridge, or
   `whisper.cpp` Metal. Hotkey / push-to-talk (no wake word on a laptop).
3. TTS: **AVSpeechSynthesizer** pl-PL voice (Swift bridge) for spoken replies.
4. Wire a TUI or a Swift menu-bar shell for input/output.
Path B (Swift): build the SwiftUI app over `jessica-ffi`; call the mesh via URLSession.

## Phase 3 — shared context (blazend-fabric)
1. Design the sync payload: memory notes, reminders, profile, book/track progress.
2. v1: rachel pulls a read-mostly snapshot from `jessica` over the fabric; verify a
   note saved on the Pi is recalled on the Mac.
3. v2: append-only deltas both directions; last-writer-wins per key.
   Grow [`../domains/blazend-fabric`](../domains/blazend-fabric/) as needed.

## Phase 4 — mesh generalisation + polish
1. Move to the node-aware `mesh:` schema ([`03-LLM-MESH.md`](03-LLM-MESH.md) §1);
   self-first ordering + per-node reachability probes.
2. Unit tests mirroring [`../rpi5/tests/unit/test_model_router.py`](../rpi5/tests/unit/test_model_router.py)
   for the multi-node ordering/fallback.
3. Any rachel-specific DSPy prompts: compile **on paul** (`../scripts/compile-prompts.py`),
   commit to `../configs/prompts/`.

## Testing / DoD
- `curl` the MLX server (Polish reply, good tokens/s).
- Pi routes a `recommend` to rachel and back (logs).
- rachel answers `open_qa`→cloud, `recommend`→local MLX, heavy→paul.
- A note crosses nodes (context sync).
- Root invariants held: Polish-first, on-device by default, cloud opt-in, PL+EN
  asset parity, no models/secrets committed.

## Coordination with the `jessica` (Pi) session
- The Pi session owns `rpi5/`, `configs/`, `domains/`. Mesh changes to
  `configs/llm.yaml` / `model_router.py` / `blazend-fabric` are **shared** — land
  them via small PRs/commits on the same branch and tell the Pi session, so the
  router stays consistent on both ends. This scaffold + the audiobook engine are in
  flight on branch `refactor/domain-architecture`.
