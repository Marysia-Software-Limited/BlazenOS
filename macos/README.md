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

## Audiobooks from Calibre (shipped — Phases A + B)
rachel turns the maintainer's **Calibre** Polish ebooks (`~/calibre`) into
chapterized **audiobooks** using **Apple on-device TTS** (Zosia; Azure Neural is
an opt-in premium path) and plays them on the Mac with resume + chapter
auto-advance — reusing the Pi's audiobook engine via two new **`domains/` common
libs** (domains for common code):
- [`../domains/audiobook-catalog`](../domains/audiobook-catalog/) — Python: the
  catalog model, spoken-title resolver, and progress store (the Pi imports the
  same lib; its own modules are now thin shims).
- [`../domains/blazend-audiobook`](../domains/blazend-audiobook/) — Rust: the
  portable playback engine (symphonia decode + resume seek + the loudness/
  compression dynamics chain + position file) behind an `AudioSink` trait. rachel
  links a **cpal** sink (`macos/player/rachel-player`); the Pi links an ALSA sink
  (Phase C, coordinated with the `paul` session).

Usage (`macos/agent`):
```bash
make -C macos venv                       # install the agent + shared lib
rachel-audiobook list                    # every Polish book in ~/calibre
rachel-audiobook render "metro 2033"     # Apple TTS → chapter MP3s → shared catalog
rachel-audiobook play "metro 2033"       # play on the Mac (resume + auto-advance)
rachel-audiobook resume                  # continue the last book
```
Rendered books land in `~/Library/Application Support/blazen/audiobooks/<slug>/`
in the shared `catalog.json` schema, so syncing them to the Pi is a copy, not a
conversion (deferred). For best quality install the **Zosia (Premium/Enhanced)**
system voice (System Settings → Accessibility → Spoken Content → System Voices).
Design + plan: [`../docs/superpowers/specs/2026-07-06-macos-audiobook-domains-design.md`](../docs/superpowers/specs/2026-07-06-macos-audiobook-domains-design.md),
[`../docs/superpowers/plans/2026-07-06-macos-audiobook-domains.md`](../docs/superpowers/plans/2026-07-06-macos-audiobook-domains.md).

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

**Status:** the **Calibre → Apple-TTS audiobook** path is built and verified
end-to-end (Phases A + B: shared `domains/` libs, `rachel-audiobook` render +
play, `rachel-player` over cpal). The **LLM mesh / MLX / context sync** remain
scaffold (design docs below). Phase C (rewiring the Pi player onto the shared
engine) is handed off to the `paul` session
([`../docs/superpowers/plans/2026-07-06-phase-c-pi-player-rewire-HANDOFF.md`](../docs/superpowers/plans/2026-07-06-phase-c-pi-player-rewire-HANDOFF.md)).
