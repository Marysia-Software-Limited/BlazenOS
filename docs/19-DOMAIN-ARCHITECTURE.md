# 19 — Domain Architecture (ports-and-adapters)

How `blazen_os` is split into **six capability domains**, why, and the multi-phase
program that takes it from today's Pi-only Python+Rust layout to a single portable
Rust core powering every platform — so many devices can act as **one personality**.

This is the structural backbone. It does **not** change any voice behaviour, intent,
or phrase. Read [`01-ARCHITECTURE.md`](01-ARCHITECTURE.md) (processes + IPC) and
[`14-RUST-PYTHON-SPLIT.md`](14-RUST-PYTHON-SPLIT.md) (language split) first.

> **Decision (2026-06-26):** the codebase is organized by **capability domain**, not by
> language or by service. Each domain is a **portable core + per-platform adapters**
> (ports-and-adapters / hexagonal). The split is rolled out Pi-first in five phases
> (see [Program roadmap](#program-roadmap)); the appliance stays green and bootable
> after every phase.

## The six domains

| # | Domain | Responsibility | Layer |
|---|--------|----------------|-------|
| 1 | **local-ai** | Embedded LLM inference (Bielik 4.5B baked in the image; Hailo later) | mind |
| 2 | **ai-orchestrator** | Route comms to/from many AIs; intent understanding + routing/escalation; conversation | mind |
| 3 | **context** | Memory, profile, reminders, resources; future cross-device sync | mind |
| 4 | **voice-input** | Capture → wake → VAD → ASR; hardware-close | body |
| 5 | **voice-output** | TTS → playback (speech + radio/stream); hardware-close | body |
| 6 | **systems** | Host/image/lifecycle/health; the platform the body runs on | body |

## Mind vs Body

- **Mind** (domains 1–3) is **device-independent**: the same routing, memory, and
  model-selection logic everywhere. It is the "personality" — and therefore the thing
  that is **shared across devices** and the eventual home of the portable Rust core.
- **Body** (domains 4–6) is **per-device**: audio I/O, GPIO, the OS image, the speech/
  TTS engines. Written **close to the hardware**, one adapter set per platform.

The trajectory for every domain is the same: **RPi5 → Nvidia Orin Nano → Android/iOS**.
A new platform implements that domain's ports; the domain's core never changes.

## Ports-and-adapters

Each domain is two parts:

```
domains/<domain>/
  core/                # portable: domain logic + Port interfaces. NO hardware/vendor imports.
    ports.py           #   the Protocols (Phase 1) that become Rust traits (Phase 2)
  adapters/
    rpi5/              # the RPi5 implementations behind those ports (ALSA, GGUF, Whisper, Piper, GPIO)
    orin/  android/  ios/   # added later — sibling adapters, core untouched
```

- A **port** is a narrow interface the core depends on; an **adapter** is a concrete
  hardware/vendor implementation of it.
- **The Port template already exists:** `blazend.assistant.localllm.Llm`
  (`available` / `chat` / `chat_stream`). `LocalLlm` (llama.cpp), `OllamaLlm` (LAN GPU),
  `OpenAI`, and `Gemini` all already satisfy it — that one Protocol, with four
  interchangeable backends, is the pattern every domain follows.
- **Language is not mixed inside a unit.** A domain folder *groups* its units; it never
  *merges* languages. Rust and Python units in the same domain stay separate processes
  talking over IPC (see below), exactly as today. This preserves
  [`14-RUST-PYTHON-SPLIT.md`](14-RUST-PYTHON-SPLIT.md) through Phase 3.

## The contract (the nervous system)

Domains do not call each other directly — they exchange **events over the existing bus**:

- **Transport:** length-prefixed JSON over Unix sockets under `/run/blazen/`.
- **Topics:** `audio.frame`, `wake.detected`, `vad.start`/`vad.end`, `asr.partial`/
  `asr.final`, `nlu.intent`, `brain.reply`, `tts.frame`, `system.event`, `error`.
- **Schema is the source of truth:** `configs/_schema/events/<topic>.schema.json`. Rust
  types come from `crates/blazend-ipc`; Python from `scripts/gen-event-types.py`.

This bus *is* the cross-domain boundary. It already isolates the Python/Rust split by
process, and it is what lets a domain be swapped, distributed, or run remotely (e.g. the
dev Ollama backend on Paul's GPU) without the rest of the system noticing.

## One personality (cross-device)

The "use it all as one personality" goal rides on the **context** domain plus the
already-present federation layer `crates/blazend-fabric` (`peer_online`, `sync_fact`,
`rpc_request`). Context records (memos, reminders, profile) replicate between devices via
`sync_fact`; because the mind is device-independent, any device reasons over the same
context. This is **designed now (Phase 1), seam-stubbed in Phase 4, fully implemented in
Phase 5** — see [`16-SYNC-PROTOCOL.md`](16-SYNC-PROTOCOL.md).

## Domain → current code (Pi)

| Domain | core (ports + logic) | adapters/rpi5 (impls) | configs |
|--------|----------------------|------------------------|---------|
| **local-ai** | `Llm` port (promoted) | `assistant/localllm.py` | `llm.yaml` |
| **ai-orchestrator** | `engine.py`, `dispatch.py`, `brain/`, backend registry | `assistant/{ollama,openai,gemini}.py`; `blazend-nlu` (Rust); `assistant/{news,weather}.py` | `intents/`, `news.yaml`, `weather.yaml` |
| **context** | memory/profile/reminders model | `assistant/{memory,embeddings}.py`; sync → `blazend-fabric` | `embeddings.yaml`, `fabric.yaml` |
| **voice-input** | `Capture`/`Wake`/`Vad`/`Asr` ports | `blazend-audio-in`, `blazend-wake`, `wakeword/`, `asr/`, `audio/`, `button/`, `blazend-audioring` (shared) | `audio.yaml`, `asr.yaml`, `wake-word.yaml`, `voice-policy.yaml` |
| **voice-output** | `Tts`/`Playback` ports | `blazend-tts`, `blazend-audio-out`, `blazend-player`, `orchestrator/radio_control.py`, `assistant/{radio,sentences}.py` | `tts.yaml`, `radio.yaml` |
| **systems** | lifecycle/health | `stage-blazen/`, systemd units, `blazend-health`, `bootstrap/`, `recovery.py`, `state/`, `orchestrator/supervisor.py`, `led*.py`, `config/` | `system.yaml` |
| **contract** (shared) | — | `blazend-ipc`, `configs/_schema/events/`, Python `ipc/`+`events/` | — |

Boundary calls (defaults): **NLU** → ai-orchestrator/understanding; **button** →
voice-input (PTT activation); **led** → systems (status); **audioring** → voice-input
(shared lib, used by output too); **`plnum.py`** (number→Polish words) → shared locale
util, imported by both `dispatch` and TTS, never duplicated.

> **Status (Phase 1 complete):** the Python modules above now live at
> `rpi5/src/blazend/domains/<domain>/adapters/rpi5/…`; the `core/ports.py` per
> domain hold the Port protocols. Only the shared contract/platform layer
> (`config`, `events`, `ipc`) stays at the top of the `blazend` package. The Rust
> adapter crates relocate under the domain tree in Phase 3.

## Program roadmap

| Phase | Title | Center | Outcome |
|-------|-------|--------|---------|
| **1** | Domain-ize the Pi | Python | 6-domain tree under `rpi5/domains/`, ports extracted, appliance green |
| **2** | Rust contract + core foundation | Rust | `blazend-ipc` hardened; `jessica-core` grows portable mind **types** (intent, context, routing) as Rust traits; `jessica-ffi` widened |
| **3** | Rewrite Rust adapter crates under domains | Rust | `blazend-{audio-in,audio-out,tts,player,wake,nlu,health,audioring}` reimplemented clean, behind their ports; binary names unchanged |
| **4** | Migrate the mind Python→Rust *(gated)* | Rust | orchestrator routing + context model move into `jessica-core`; Pi keeps only ML glue. **Revises §6 of [`14-RUST-PYTHON-SPLIT.md`](14-RUST-PYTHON-SPLIT.md) — needs sign-off** |
| **5** | Cross-platform + cross-device | Rust everywhere | `jessica-core` powers Orin/Android/iOS; `blazend-fabric` `sync_fact` → one personality; shared cores hoist to repo-root `domains/` |

One phase at a time, `make test-fast` green between each. Phase 4 is the load-bearing
decision: it deliberately moves the orchestrator (today Python-mandated) into Rust, and
ships only after that doc is updated and the maintainer approves.

> **Status (Phase 2 in progress):** the portable **context** mind types landed in
> `crates/jessica-core/src/context.rs` — `Note`, `Reminder`, `ReminderCategory`, the
> `MemoryStore` trait (the Rust mirror of the Python `MemoryStorePort`), and an
> `InMemoryStore` reference impl mobile cores reuse. Joins the pre-existing `intent`
> router as the second mind leg; **routing** types, the `blazend-ipc` hardening, and the
> `jessica-ffi` widening are the remaining Phase 2 work. The Python store stays the Pi's
> sibling adapter behind the same port; JSON shapes are kept interchangeable.
