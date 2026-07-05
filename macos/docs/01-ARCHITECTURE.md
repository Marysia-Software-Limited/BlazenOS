# 01 — rachel architecture (macOS node)

How the macOS Jessica node is put together. rachel is a **full agent host** (like
the Pi appliance), not a phone app — but on macOS it can be a lightweight
menu-bar/daemon rather than a kiosk.

## Stack overview

```
┌─────────────────────────── rachel (MacBook, Apple Silicon) ───────────────────────────┐
│                                                                                        │
│  UI: Swift menu-bar app (SwiftUI)  ──────────────┐                                      │
│    • push-to-talk / hotkey, transcript, status   │ FFI (C ABI)                          │
│    • talks to jessica-core for intents/memory  ──┘──►  domains/jessica-core (Rust)      │
│                                                        (intents, memory, routing types) │
│                                                                                        │
│  Agent core (choose ONE host language — see §Decision):                                 │
│    • Python orchestrator (reuse rpi5 ModelRouter + prompts.py + tools)   ── OR ──        │
│    • Swift orchestrator calling jessica-core + an HTTP client                            │
│                                                                                        │
│  ML on Apple Silicon:                                                                    │
│    • LLM  → MLX (mlx-lm) server  OR  llama.cpp Metal server  → OpenAI-compatible :PORT   │
│    • ASR  → Apple Speech framework (on-device)  OR  whisper.cpp Metal                    │
│    • TTS  → AVSpeechSynthesizer (has pl-PL voices)  OR  shared Piper                     │
│                                                                                        │
│  Mesh + context:                                                                        │
│    • ModelRouter (shared) with rachel's MLX endpoint + peer node URLs                    │
│    • blazend-fabric SyncLog  ──── LAN ────►  jessica (Pi)  +  paul (Linux)               │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

## §Decision — host language for the agent core
Two viable paths; pick per the user's preference in Phase 0:

- **A) Python orchestrator (recommended for speed of reuse).** Run the appliance's
  Python agent stack on macOS (it's already cross-platform: `ModelRouter`,
  `prompts.py`, `tools.py`, the RAG). Swap the ML adapters for Apple ones (MLX LLM
  endpoint, Apple Speech, AVSpeech TTS). Fastest way to a working rachel that
  shares the exact routing + prompts + RAG with jessica. UI is a thin Swift
  menu-bar app over a local socket, or a TUI to start.
- **B) Native Swift agent.** A Swift app calling `jessica-core` via
  `jessica-ffi` (like `ios/`), with Swift ML glue. More "Mac-native," more work,
  diverges from the Python stack. Better as a *later* polish once A proves the mesh.

**Recommendation:** start with **A** — reuse the Python agent + shared configs;
add a Swift menu-bar shell for UX. Revisit B only if a native app is a goal.

## ML on Apple Silicon

- **LLM (the headline feature).** Run **MLX** (`mlx-lm`, Apple's array framework)
  or **llama.cpp with Metal**. Both can serve an **OpenAI-compatible HTTP API**
  (`mlx_lm.server`, `llama-server`) — that endpoint is what plugs into the
  `ModelRouter` as a backend named e.g. `rachel-mlx`. Models: Bielik (Qwen2.5-based
  — MLX supports Qwen), Qwen2.5, Llama 3.x. Apple Silicon runs 4–8B comfortably and
  *fast* (unified memory), so rachel can host the **4.5B recommend tier** and even
  an 8B — the tier that's painfully slow on the Pi CPU.
- **ASR.** Prefer **Apple Speech framework** (`SFSpeechRecognizer`, on-device
  Polish) for the Mac's own mic; or whisper.cpp Metal to match the appliance
  exactly. rachel doesn't need the wake-word pipeline — a hotkey / push-to-talk is
  fine on a laptop.
- **TTS.** **AVSpeechSynthesizer** has good pl-PL system voices (on-device, free) —
  ideal for rachel's spoken replies. The Azure/ElevenLabs *book-rendering* pipeline
  stays on paul; rachel just needs conversational TTS.

## Networking
- rachel joins the LAN; discover peers by static config first (node URLs in
  `configs/llm.yaml` `routing:` / a new `configs/mesh.yaml`), mDNS/Bonjour later.
- rachel's MLX endpoint is reachable by `jessica`/`paul` (so they can offload to
  the Mac when it's on); conversely rachel routes to paul's 11B for heavy tasks and
  cloud for `open_qa`.
- Security: LAN-only, no auth to start (like the current Pi→paul Ollama link);
  add a shared token before anything leaves the LAN.

## Context sharing (`blazend-fabric`)
- **What syncs:** memory notes, reminders, user profile/name, and per-book/-track
  progress — so "co zapamiętałam", reminders, and "czytaj dalej" work from any node.
- **How (phased):** (1) rachel reads a shared snapshot pulled from jessica over the
  fabric; (2) append-only SyncLog so each node publishes deltas; (3) conflict
  handling (last-writer-wins per key → CRDT if needed). `blazend-fabric` is the home;
  it's currently minimal, so expect to grow it.
- **Privacy:** context stays on the LAN between the user's own nodes; no cloud.

## What rachel deliberately does NOT do
- No wake-word / always-listening (that's the Pi appliance's job); rachel is
  hotkey-driven.
- No book *rendering* (Azure/ElevenLabs) — that's paul's offline pipeline; rachel
  only *plays/reads* via Apple TTS or shares the rendered audiobooks.
- No hard coupling: if rachel is asleep/closed, jessica + paul are unaffected.
