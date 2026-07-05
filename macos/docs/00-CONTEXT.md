# 00 — Context for the macOS (rachel) build session

Read this first. It hands a fresh Claude session (running on the MacBook)
everything it needs to build the **rachel** node without re-reading the whole
monorepo. Written by the `jessica` (Raspberry Pi) session that built the appliance
+ the LLM router + the book/music RAG.

---

## 1. What Jessica is

**Jessica** is a **voice-first, on-device Polish assistant.** Today she lives on a
Raspberry Pi 5 appliance (`../rpi5/`): wake word → ASR (Whisper) → NLU (Rust
regex) → orchestrator → LLM (Bielik) → TTS (Piper), all local; radio + a
1526-book Wolne Lektury audiobook library + music + semantic recommendations. The
codebase is a **monorepo organised by capability domain**: portable Rust cores at
the repo root ([`../domains/`](../domains/)) + per-platform adapters
(`rpi5/`, `android/`, `ios/`, and now `macos/`). Root `AGENTS.md` / `CLAUDE.md`
are the cross-agent baseline; **read them** — they carry hard invariants
(Polish-first runtime, on-device ML, the CPU path is the contract, etc.).

## 2. The constellation (nodes)

| Node | Machine | LAN | Role | Local AI today |
|------|---------|-----|------|----------------|
| **jessica** | Raspberry Pi 5 (8 GB) | `.24` + USB `10.55.0.1` | always-on voice appliance | Bielik 1.5B/4.5B (llama.cpp CPU) |
| **paul** | Linux, RTX 3090, native Arch | `192.168.50.102` | GPU + build rig | **Ollama Bielik 11B** `:11434`, Whisper `:8090`, cloud keys |
| **rachel** | MacBook (Apple Silicon), host **`rebeca`** | TBD | interactive desktop Jessica | **to build:** MLX / Metal LLM |

Hardware roadmap: an **NVIDIA Jetson Orin Nano** will later become jessica's brain;
a Hailo/phone accelerator was evaluated + rejected (see the `jessica` session's
memory). rachel is the **first non-Linux full node** and the first to use **Apple
Silicon** for inference.

### Naming — CONFIRM WITH THE USER
The user wrote: *"nodes: RPI5(jessica), MacOS(rachel), Linux(paul) … then I'll
start other Claude session on the rebeca."* This scaffold assumes:
- **rachel** = the macOS Jessica node/agent name (parallel to `jessica`, `paul`).
- **rebeca** = the MacBook's hostname (where your session runs).

If the user meant one name for both, unify it in this doc + `README.md` before
building.

## 3. What the user asked for (verbatim intent)

> "I want a MacOS node, want to use MacOS AI acceleration and connect with RPI5 by
> network, use DSPy to share LLM between the nodes: RPI5(jessica), MacOS(rachel),
> Linux(paul), instead of ollama or with ollama. I want also to be able to use
> jessica agent with my macbook too, with shared resources and context with all
> nodes."

Decoded into requirements:
1. **rachel runs a Jessica agent on the Mac** you can actually talk to / use.
2. **Apple-Silicon acceleration** for its ML (LLM + ideally ASR/TTS).
3. **Networked** with the Pi (and paul).
4. **DSPy-shared LLM mesh** across all three nodes — Ollama becomes *one* backend,
   not the only one; DSPy programs run against the best available model in the pool.
5. **Shared resources + context** — memory/conversation state synced so any node
   answers with the same context.

Also decided this session: **TTS for reading ebooks** starts on **Azure Neural
(pl-PL)**, with **ElevenLabs later for selected books** (premium). That's a
rendering pipeline on `paul`, not a rachel concern — but rachel may later host its
own Apple `AVSpeechSynthesizer` Polish TTS.

## 4. What already exists that rachel REUSES (don't rebuild)

- **`ModelRouter`** — [`../rpi5/src/blazend/domains/ai_orchestrator/core/model_router.py`](../rpi5/src/blazend/domains/ai_orchestrator/core/model_router.py).
  Task-based routing (`command`→1.5B, `recommend`→4.5B, `open_qa`→gpt-5.5, all
  prefer the reachable Ollama-11B). Backends share a simple `Llm` protocol
  (`available` / `chat` / `chat_stream`). **rachel's MLX LLM becomes a new backend
  here; the mesh generalises this to multi-node.** Routing table is data-driven in
  [`../configs/llm.yaml`](../configs/llm.yaml) `routing:`.
- **DSPy compile pipeline** — [`../scripts/compile-prompts.py`](../scripts/compile-prompts.py)
  optimises signatures OFFLINE (BootstrapFewShot, Ollama-11B teacher) → static
  JSON in [`../configs/prompts/`](../configs/prompts/); the runtime loads them via
  [`../rpi5/src/blazend/domains/ai_orchestrator/adapters/rpi5/assistant/prompts.py`](../rpi5/src/blazend/domains/ai_orchestrator/adapters/rpi5/assistant/prompts.py)
  with **no dspy dependency at runtime**. rachel runs the *same* compiled prompts.
- **`domains/jessica-core`** (Rust mind) + **`jessica-ffi`** (C ABI / cbindgen
  header for Swift) — the intent/memory/routing contract, identical across
  surfaces. See [`../docs/17-MOBILE-MONOREPO.md`](../docs/17-MOBILE-MONOREPO.md).
- **`domains/blazend-fabric`** — the cross-node **SyncLog** substrate (how context
  will sync between nodes). Currently minimal; rachel is a good forcing function.
- **`domains/blazend-ipc`** — the local IPC bus (`nlu.intent`, `brain.reply`, …)
  the appliance's services speak over.

## 5. The target: a shared LLM mesh + shared context

Two planes:

**Model plane (DSPy mesh).** Each node advertises its LLM backend(s) as an
OpenAI/Ollama-compatible network endpoint. A shared registry (extend
`configs/llm.yaml` `routing:` with per-node backend URLs) lets the `ModelRouter`
pick the best backend **across the mesh** for each DSPy task — e.g. a `command`
prefers the *nearest fast* model (rachel's MLX when you're on the Mac; jessica's
1.5B on the Pi), `open_qa` prefers cloud, heavy reasoning prefers paul's 11B. DSPy
is the program layer; the router is the transport/selection layer. Full design in
[`03-LLM-MESH.md`](03-LLM-MESH.md).

**Context plane (fabric).** Memory (notes, reminders, profile, book/music progress)
and recent conversation sync over `blazend-fabric` so any node answers with the
same context. Start read-mostly (nodes read a shared snapshot); grow to CRDT/append
sync. Details in [`01-ARCHITECTURE.md`](01-ARCHITECTURE.md) §Context.

## 6. Your job (the macOS session)

1. Confirm naming (§2) with the user.
2. Read [`01-ARCHITECTURE.md`](01-ARCHITECTURE.md), [`03-LLM-MESH.md`](03-LLM-MESH.md),
   then follow [`02-BUILD-INSTRUCTIONS.md`](02-BUILD-INSTRUCTIONS.md).
3. **Phase 1 first:** stand up rachel's **MLX LLM server** and register it in the
   mesh so `jessica`/`paul` can route to it — the smallest end-to-end win. Then the
   Swift menu-bar agent, then context sync.
4. Keep every hard invariant from the root `AGENTS.md`/`CLAUDE.md` (Polish-first,
   on-device by default, PL+EN asset parity). Cloud is opt-in per-feature (Azure
   TTS, GPT-5.5) — same as the appliance.

## 7. Don'ts
- Don't reimplement `jessica-core` / the router / the DSPy prompts in Swift — bind
  or call them.
- Don't make rachel a hard dependency of `jessica`: the Pi must stay fully
  functional standalone (mesh is strict-improvement, like the accelerator path).
- Don't commit models or secrets. Cloud keys live in a local secrets file, never git.
