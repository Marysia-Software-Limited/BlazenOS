# 19 — Domain Architecture (ports-and-adapters)

How `blazen_os` is split into **six capability domains**, why, and the program that
takes it from today's Pi-first layout to a single set of portable domain libraries
powering every platform — so many devices can act as **one personality**.

This is the structural backbone. It does **not** change any voice behaviour, intent,
or phrase. Read [`01-ARCHITECTURE.md`](01-ARCHITECTURE.md) (processes + IPC) and
[`14-RUST-PYTHON-SPLIT.md`](14-RUST-PYTHON-SPLIT.md) (language split) first.

> **Decision (2026-06-29, revises 2026-06-26):** the codebase is organized by
> **capability domain**, not by language or by platform. Each domain is a **portable
> core library + per-platform adapters** (ports-and-adapters / hexagonal). The two parts
> live in two different places:
>
> - **Portable cores are common libraries at the repo root**, under `domains/`. They are
>   device-independent and shared by every platform.
> - **Platform-specific code lives under the platform dirs** — `rpi5/`, `android/`,
>   `ios/`. Each platform implements only the adapters its domains need, in that
>   platform's language, against that platform's hardware/OS.
>
> This supersedes the earlier decision to nest the shared cores under `rpi5/`: the cores
> belong to no single platform, so they sit above all of them. The rollout stays
> Pi-first and phased; the appliance stays green and bootable after every step.

## The organizing principle

> **A domain is a common library, not a folder inside one platform.** If code is
> device-independent — routing, memory, intent, the contract — it is a root `domains/`
> library every platform links. If code only makes sense on one platform — ALSA, GPIO,
> a GGUF loader, Apple Speech — it is an adapter under `rpi5/`, `android/`, or `ios/`.
> The boundary between the two is a **port**: a narrow interface the core owns and the
> platform implements.

```
domains/                 # repo root — portable domain cores = the common libraries
  <domain>/              #   ports + device-independent logic. NO hardware/vendor imports.

rpi5/    android/    ios/ # per-platform: only the adapters that satisfy those ports
  <domain>/...           #   ALSA / GGUF / Whisper / Piper / GPIO  |  Kotlin+Google ML  |  Swift+Apple ML
```

## The six domains

| # | Domain | Responsibility | Layer |
|---|--------|----------------|-------|
| 1 | **local-ai** | Embedded LLM inference (Bielik 4.5B baked in the image; Hailo later) | mind |
| 2 | **ai-orchestrator** | Route comms to/from many AIs; intent understanding + routing/escalation; conversation | mind |
| 3 | **context** | Memory, profile, reminders, resources; cross-device sync | mind |
| 4 | **voice-input** | Capture → wake → VAD → ASR; hardware-close | body |
| 5 | **voice-output** | TTS → playback (speech + radio/stream); hardware-close | body |
| 6 | **systems** | Host/image/lifecycle/health; the platform the body runs on | body |

Plus the **contract** — the IPC nervous system that joins them (see below). It is itself
a shared root library, not a domain.

## Mind vs Body

- **Mind** (domains 1–3) is **device-independent**: the same routing, memory, and
  model-selection logic everywhere. Its cores carry **real portable logic**; the
  per-platform adapters are thin. The mind is the "personality" — the thing **shared
  across devices**.
- **Body** (domains 4–6) is **per-device**: audio I/O, GPIO, the OS image, the speech/
  TTS engines. Its cores carry mostly **port definitions** (the contracts); the weight
  is in the platform adapters, written **close to the hardware**.

Either way the root `domains/<domain>/` library holds the portable part and the ports;
each platform dir holds that platform's adapters. The trajectory for every domain is the
same: **RPi5 → Nvidia Orin Nano → Android/iOS**. A new platform implements a domain's
ports; the domain's core never changes.

## Ports-and-adapters across the tree

```
domains/<domain>/          # PORTABLE core (repo root): the Protocols/traits + logic.
                           #   No hardware or vendor imports. The single source of truth.
rpi5/<domain>/             # the RPi5 adapters behind those ports (ALSA, GGUF, Whisper, Piper, GPIO)
android/<domain>/          # the Android adapters (Kotlin + Compose, Google Speech / Gemini Nano)
ios/<domain>/              # the iOS adapters (Swift + SwiftUI, Apple Speech / Foundation Models)
```

- A **port** is a narrow interface the core depends on; an **adapter** is a concrete
  hardware/vendor implementation of it, living under the platform that provides it.
- **The Port template already exists:** `Llm` (`available` / `chat` / `chat_stream`)
  in the local-ai domain. `LocalLlm` (llama.cpp), `OllamaLlm` (LAN GPU), `OpenAI`, and
  `Gemini` all satisfy it — that one Protocol, with four interchangeable backends, is the
  pattern every domain follows. The Rust mirror landed in Phase 2
  (`jessica-core::{LlmPort-shaped types, MemoryStore, RoutePlan, IntentRouter}`).
- **Language is not mixed inside a unit.** A domain folder *groups* its units; it never
  *merges* languages. Rust and Python units in the same domain stay separate processes
  talking over IPC, exactly as today. This preserves
  [`14-RUST-PYTHON-SPLIT.md`](14-RUST-PYTHON-SPLIT.md) through the migration.

## The contract (the nervous system)

Domains do not call each other directly — they exchange **events over the existing bus**:

- **Transport:** length-prefixed JSON over Unix sockets under `/run/blazen/`.
- **Topics:** `audio.frame`, `wake.detected`, `vad.start`/`vad.end`, `asr.partial`/
  `asr.final`, `nlu.intent`, `brain.reply`, `tts.frame`, `system.event`, `error`.
- **Schema is the source of truth:** `configs/_schema/events/<topic>.schema.json`. Rust
  types come from the contract library (`blazend-ipc`); Python from
  `scripts/gen-event-types.py`.

This bus *is* the cross-domain boundary, and the contract library is a **root `domains/`
library** (it belongs to no platform). It already isolates the Python/Rust split by
process, and it is what lets a domain be swapped, distributed, or run remotely (e.g. the
dev Ollama backend on Paul's GPU) without the rest of the system noticing. Phase 2
hardened it (fail-closed protocol-version check, safe broadcast, topic-vs-schema drift
guard).

## One personality (cross-device)

The "use it all as one personality" goal rides on the **context** domain plus the
federation layer `blazend-fabric` (`peer_online`, `sync_fact`, `rpc_request`) — both root
`domains/` libraries. Context records (memos, reminders, profile) replicate between
devices via `sync_fact`; because the mind is a shared library, every device reasons over
the same context. The portable memory model (`Note`, `Reminder`, `MemoryStore`) and its
FFI seam landed in Phase 2; full replication is **seam-stubbed in Phase 4, fully
implemented in Phase 5** — see [`16-SYNC-PROTOCOL.md`](16-SYNC-PROTOCOL.md).

## Domain → libraries

The portable core of each domain is a root `domains/` library; the Pi's current
implementation is its first adapter set. (Android/iOS adapter columns fill in at Phase 5.)

| Domain | Portable core (`domains/`) | RPi5 adapters (`rpi5/`) | configs |
|--------|----------------------------|--------------------------|---------|
| **local-ai** | `Llm` port + selection logic | `localllm.py` (llama.cpp/Bielik) | `llm.yaml` |
| **ai-orchestrator** | intent router, `RoutePlan`, dispatch model (`jessica-core`) | `engine.py`, `brain/`, `{ollama,openai,gemini}.py`, `blazend-nlu` (Rust) | `intents/`, `news.yaml`, `weather.yaml` |
| **context** | memory model + `MemoryStore` port; `blazend-fabric` sync | `memory.py`, `embeddings.py` | `embeddings.yaml`, `fabric.yaml` |
| **voice-input** | `Capture`/`Wake`/`Vad`/`Asr` ports | `blazend-audio-in`, `blazend-wake`, `wakeword/`, `asr/`, `blazend-audioring` | `audio.yaml`, `asr.yaml`, `wake-word.yaml` |
| **voice-output** | `Tts`/`Playback` ports | `blazend-tts`, `blazend-audio-out`, `blazend-player`, `radio_control.py` | `tts.yaml`, `radio.yaml` |
| **systems** | lifecycle/health contract | `stage-blazen/`, systemd units, `blazend-health`, `bootstrap/`, `recovery.py` | `system.yaml` |
| **contract** (shared) | `blazend-ipc`, `configs/_schema/events/` | Python `ipc/`+`events/` | — |

**Boundary calls (defaults):** **NLU** → ai-orchestrator (understanding); **button** →
voice-input (PTT activation); **LED** → systems (status); **audioring** → voice-input
(shared lib, used by output too); **`plnum.py`** (number→Polish words) → shared locale
util, imported by both `dispatch` and TTS, never duplicated.

## Program roadmap

Phases 1–2 are complete. Phase 3 onward executes the cores-at-root direction.

| Phase | Title | Center | Outcome |
|-------|-------|--------|---------|
| **1** ✅ | Domain-ize the Pi | Python | 6-domain tree, ports extracted, appliance green *(landed under `rpi5/src/blazend/domains/`; Phase 3 hoists the portable parts to root)* |
| **2** ✅ | Rust contract + core foundation | Rust | `jessica-core` grew portable mind types (intent, context, routing); `blazend-ipc` hardened; `jessica-ffi` widened |
| **3** | Establish the root `domains/` library tree | Rust | The shared cores (`blazend-ipc`, `blazend-fabric`, `jessica-core`, `jessica-ffi`) move from `crates/` into `domains/<domain>/`; the Pi adapter crates move from `rpi5/crates/` into `rpi5/<domain>/`. Workspaces re-rooted, **binary names unchanged**, appliance green |
| **4** | Migrate the mind Python→Rust *(gated)* | Rust | orchestrator routing + context model move out of the Pi's Python and into the root `domains/` cores; the Pi keeps only ML glue + adapters. **Revises §6 of [`14-RUST-PYTHON-SPLIT.md`](14-RUST-PYTHON-SPLIT.md) — needs sign-off** |
| **5** | Cross-platform + cross-device | Rust everywhere | `android/` and `ios/` implement their domain adapters against the **same** root cores; `blazend-fabric` `sync_fact` → one personality |

One phase at a time, `make test-fast` green between each. Phase 4 is the load-bearing
decision: it deliberately moves the orchestrator (today Python-mandated) into Rust, and
ships only after that doc is updated and the maintainer approves.

### Phase 3 — execution notes

The move is mechanical and low-risk because it is a **source relocation, not a rewrite**:
binary names, runtime behaviour, and the event contract are untouched.

- **Root `domains/` workspace.** The shared-core Cargo workspace at `crates/` becomes the
  `domains/` workspace; its members are the per-domain libraries. Mobile build scripts
  (`build-ios-xcframework.sh`, `build-android-jnilibs.sh`) re-point at the new paths.
- **Pi adapters.** The appliance workspace manifest **stays discoverable where the build
  expects it** (everything does `cd <appliance-workspace> && cargo … --workspace` and
  reads `…/target/<triple>/release/<bin>`). Crate **directories** move under
  `rpi5/<domain>/`; `members` paths and the one sibling-relative dep
  (`blazend-audioring`) are updated — shared-core deps are workspace-inherited
  (root-relative) and survive the move untouched.
- **`blazend-audioring`** is cross-domain (voice-input + voice-output); it lives under
  `voice-input` as its nominal owner and is consumed by voice-output via a workspace dep.
- **Verification:** `cargo build/test/clippy/fmt --workspace` + `make lint` are fully
  host-runnable and prove the relocation. The aarch64 cross-build and `make test-vm`
  (QEMU TCG, blocked on the WSL host) are unaffected by a pure source move — the one
  thing to smoke-test on real hardware is that the image still boots the renamed-path
  build (binary names and the install path `/usr/lib/blazen/bin/` do not change).
