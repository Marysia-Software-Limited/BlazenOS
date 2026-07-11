# `linux/` — Jessica on Linux nodes (paul now, the Pi later)

A **Linux node surface** in the blazen_os constellation. Where `rpi5/` is the
always-on **voice appliance** and `macos/` (rachel) is the **desktop** agent,
`linux/` is the **server** form of Jessica: reached over a CLI/REPL/socket (no
wake word), reusing the portable engine and wiring its adapters to whatever the
node advertises on the **mesh**.

Today it targets **paul** (Linux + RTX 3090): the LLM is paul's GPU **Ollama-11b**
resolved from [`../configs/mesh.yaml`](../configs/mesh.yaml), TTS is paul's
**XTTS-v2** service, ASR is the remote **Whisper**. The same surface is intended
to run on the Pi as a Linux box later.

## Layout
- `agent/` — the Python agent (`jessica-linux`): `node.py` wires the mesh →
  `ModelRouter`/`Assistant`; `cli.py` is the `jessica` front door. Tests under
  `agent/tests/`.

## The reuse rule
The conversational **engine is not forked** — `agent/` imports the portable
`Assistant` (memory, routing, RAG, prompts) from the `blazen_os` package
(`rpi5/src/blazend`, editable-installed by `make python`) and the shared
`domains/` libs (`mesh-registry`, `audiobook-catalog`). Only the **adapters**
(which LLM/TTS/ASR) are node-specific, and those come from the mesh. When the mind
migrates to `domains/` (Phase 4), the import path moves; the surface does not.

## Run
```sh
make python                              # installs the agent + shared libs
BLAZEN_NODE=paul jessica "co potrafisz?" # one-shot, answers in Polish via Ollama-11b
BLAZEN_NODE=paul jessica --voice "…"     # …and speaks the reply via the mesh XTTS
BLAZEN_NODE=paul jessica --speak "cześć" # pure TTS: render+play text via XTTS (no LLM)
BLAZEN_NODE=paul jessica                 # interactive REPL
BLAZEN_NODE=paul scripts/mesh-check.py   # (repo root) verify mesh membership + reachability
```

**Voice** (`voice.py`): the TTS endpoint is resolved from the mesh (paul's XTTS-v2
on the GPU); playback reuses `blazend-player` with the speech compressor + leveler
(build it once: `cd rpi5/crates && cargo build --release -p blazend-player`).
Override the ALSA output with `$BLAZEN_AUDIO_DEVICE` (on a PipeWire desktop use
`pulse`) and the player with `$BLAZEN_PLAYER_BIN`.

**Books / audiobooks** (`books.py`, `pip install -e linux/agent[calibre]`): resolve
a Calibre ebook by title, extract its EPUB chapters, **clean the text** for TTS
(`clean_text` — strips soft hyphens, folds the HTML extractor's mid-paragraph line
breaks, drops `(...)` marks + page numbers), chunk it, and render via the mesh XTTS.
- `jessica --read "<title>"` — play aloud (prefers already-rendered files, else
  renders live; **auto-resumes** from saved progress). Long reads → the
  `jessica-read@.service` user unit (owns PipeWire, survives shells).
- `jessica --ingest "<title>"` — render to **kept** per-chapter MP3s under
  `$BLAZEN_AUDIOBOOKS_DIR` (`~/audiobooks/<slug>/NNN.mp3`) + `catalog.json`.
  Resumable (skips rendered chapters).
- `jessica --serve-media` — serve the library on `:7477` so **other nodes stream**
  it (the paul `media` mesh resource). e.g. the Pi's catalog points a book's
  chapters at `http://192.168.50.102:7477/<slug>/NNN.mp3` and its `blazend-player`
  streams them.
- **Batch** (`scripts/render-literatura.py` + `render-literatura.service`): render
  every Calibre `literatura` book without audio — resumable per-book (manifest) and
  per-chapter, one at a time, for the long haul.
- **Nightly summary** (`scripts/render-summary.py` + `render-summary.timer`, 03:30):
  folds the batch manifest into a dated `render-YYYY-MM-DD` note (done / in-progress
  / failed, with failed titles) in this node's `memory.json`, so the summary rides
  the fabric to every node — no tailing logs on paul to see how the render is going.

Progress is an `AudiobookProgress` in the library and rides the fabric, so a book's
position syncs across nodes. Systemd unit templates live in `linux/systemd/`.

**Shared context** (`fabric.py` + the `context-sync` domain): one Jessica across
nodes. Each node serves its context snapshot (memory notes/reminders/profile +
audiobook progress) and pulls peers' snapshots from the mesh's `fabric` resources,
merging deterministically (union-by-id, last-writer-wins). Read-mostly v1;
strict-improvement (an offline peer is skipped).
```sh
BLAZEN_NODE=paul jessica --serve-fabric   # serve this node's snapshot on :7475
BLAZEN_NODE=paul jessica --sync           # pull peers + merge into local memory
```
A note saved on the Pi is then recalled on paul (and vice-versa). **Live across
machines:** the Pi runs `blazend-fabric-snapshot.service` (enabled) serving its
context on `192.168.50.24:7475`; `BLAZEN_NODE=paul jessica --sync` on paul pulls
and recalls the Pi's notes — verified end-to-end. A fresh Pi image must bake the
same wiring: install the `audiobook-catalog` / `mesh-registry` / `context-sync`
domain packages + `linux/agent` into the appliance venv, deploy `mesh.yaml` to
`/etc/blazen/`, and ship `blazend-fabric-snapshot.service` (see
`rpi5/stage-blazen/`). Live install today is additive (it did not touch the
running voice services).

**GPU fleet** (`fleet.py`): paul owns the lifecycle of the shared GPU services it
advertises in `mesh.yaml` (each carries a `unit:` — `ollama` / `blazen-whisper` /
`blazen-xtts`). `status` probes each (`systemctl is-active` + reachability +
`nvidia-smi` VRAM); `start`/`stop`/`restart` drive systemctl; `serve` exposes
`GET :7476/fleet/health` (a `health` mesh resource) so peers see one liveness view
and route around a down service (the router already skips unreachable backends —
see P3).
```sh
BLAZEN_NODE=paul jessica --fleet status    # health + VRAM (also: verify | start | stop | restart | serve)
make fleet-status                          # same, from the repo root
```

## Rules
Root [`../AGENTS.md`](../AGENTS.md) + [`../CLAUDE.md`](../CLAUDE.md) are the
baseline (Polish-first, on-device by default, mesh is strict-improvement — never
break the Pi standalone, `domains/` for common code). `make test-fast` is the gate.
Mesh design: [`../macos/docs/03-LLM-MESH.md`](../macos/docs/03-LLM-MESH.md);
domain layout: [`../docs/19-DOMAIN-ARCHITECTURE.md`](../docs/19-DOMAIN-ARCHITECTURE.md).
