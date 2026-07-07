# Handoff — build the **rachel** node into a full member of the constellation

> **FOR THE `rachel` CLAUDE SESSION (macOS, Apple Silicon, host `rebeca`).**
> Written by the `paul` (Linux, RTX 3090) session that built paul into a full
> Jessica node and stood up the **shared audiobook** pipeline. Your job: make
> rachel consume + contribute to the constellation — starting with the shared
> audiobooks — toward the user's target, **"full Jessica everywhere."**

Read this whole doc, then the linked ones. Everything is on **`main`** (canonical
now — the `refactor/domain-architecture` work was merged). `git pull` first.

---

## 1. The constellation today

| Node | Machine | Role | State |
|------|---------|------|-------|
| **jessica** | Raspberry Pi 5 | always-on **voice appliance** | full agent; pulls the shared catalog on a 15-min timer |
| **paul** | Linux + RTX 3090 | **GPU / render / share** node | full node: LLM (Ollama-11b), ASR (Whisper), **TTS (XTTS-v2)**, fabric, fleet health, **media (audiobook library)** |
| **rachel** | MacBook (Apple Silicon) | **desktop** Jessica | **your job** — today it renders Calibre→audio (`macos/agent`) but doesn't yet *consume* the shared library or serve context |

The mesh is one file — [`configs/mesh.yaml`](../../../configs/mesh.yaml) — loaded by the
shared [`domains/mesh-registry`](../../../domains/mesh-registry/) lib. Every node
advertises resources under `llm` / `asr` / `tts` / `fabric` / `health` / `media`;
consumers resolve them by `(category, name)` and never hardcode IPs. paul serves:

```yaml
paul:
  resources:
    tts:    { xtts:       { url: "http://192.168.50.102:8091/synthesize", ... } }
    media:  { audiobooks: { kind: http, url: "http://192.168.50.102:7477/" } }   # ← the shared library
    fabric: { snapshot:   { url: "http://192.168.50.102:7475/fabric/snapshot" } }
    health: { fleet:      { url: "http://192.168.50.102:7476/fleet/health" } }
```

## 2. What paul built that rachel plugs into (don't rebuild)

- **Shared audiobook library + catalog publish/merge.** paul renders Calibre books
  to XTTS MP3s (`~/audiobooks/<slug>/NNN.mp3`), serves them over HTTP
  (`jessica --serve-media`, :7477), and publishes a **URL-rewritten catalog** at
  `GET /catalog.json` (only books it actually holds). A **`literatura` batch**
  (253 books) is rendering now. Design: [`macos/docs/05-SHARED-AUDIOBOOKS.md`](../../../macos/docs/05-SHARED-AUDIOBOOKS.md).
- **The catalog merge** — `books.pull_catalog()` / `jessica --pull-catalog`
  ([`linux/agent/src/jessica_linux/books.py`](../../../linux/agent/src/jessica_linux/books.py)):
  fetch every mesh `media` peer's catalog and upsert by slug into the local one.
  The Pi runs it on a systemd timer; rachel gets a **launchd** equivalent (below).
- **The `jessica` CLI runs on macOS** — its mesh/media/audiobook subcommands
  (`--pull-catalog`, `--serve-media`, `--read`, `--ingest`, `--fleet`, `--speak`,
  `--serve-fabric`) import **no `blazend`** (the rpi5 engine), so they work without
  the appliance package. Only the chat agent (`jessica "<prompt>"` / REPL) needs
  `blazend`, which isn't on the Mac — use rachel's own path for chat.
- **Shared domains** (pip-installable, pure Python): `mesh-registry`,
  `audiobook-catalog` (catalog/resolver/**progress**), `context-sync` (the memory
  merge model). `blazend-fabric` is the context substrate.

## 3. Your job — phased, each testable

### R0 — Consume the shared audiobooks (smallest real win, do first)
1. Install the tooling into rachel's venv (pure Python, no `blazend` needed):
   ```sh
   pip install -e domains/mesh-registry domains/audiobook-catalog domains/context-sync linux/agent
   ```
2. Merge paul's catalog into rachel's:
   ```sh
   BLAZEN_NODE=rachel BLAZEN_AUDIOBOOKS_CATALOG=~/audiobooks/catalog.json jessica --pull-catalog
   ```
   Books paul rendered now resolve on rachel by title, with chapters as paul URLs.
3. **Play** a chapter by streaming its URL through `macos/player` (`rachel-player`,
   cpal) — it already takes a source + `--compress`, exactly like the Pi's
   `blazend-player`. Auto-advance `chapters[]`; save position with
   `audiobook_catalog.AudiobookProgress` (keyed by slug).
   - **DoD:** on rachel, resolve "Metro 2033" (or any batch book) and hear it play
     from paul; nothing re-rendered locally.

### R1 — Periodic pull (the macOS half of the shared-catalog timer)
- Install the launchd agent [`macos/launchd/org.blazen.pull-catalog.plist`](../../../macos/launchd/org.blazen.pull-catalog.plist)
  (adjust paths to your checkout/user), `launchctl load` it — pulls every 15 min so
  batch-rendered books appear automatically. **DoD:** a book rendered on paul shows
  up on rachel within ~15 min with no manual step.

### R2 — Full node: serve rachel's context (the heart of "one Jessica")
- rachel already advertises a `fabric` endpoint in the mesh (`:7475`). **Serve it**
  so a note saved on rachel syncs to the Pi/paul and back. The transport is
  `jessica_linux.fabric.make_server(node="rachel", memory_path=…, progress_path=…)`
  — `blazend`-free; point it at rachel's own memory JSON (rachel's agent owns that
  store). Merge peers with `fabric.pull_and_merge`. The model is `context_sync`
  (`Snapshot`/`merge`, last-writer-wins). **DoD:** a memory saved on any node is
  recalled on the other two.
- Optionally advertise rachel's own `media` (Apple/Azure renders) + `tts` so paul/
  the Pi can use rachel's voice.

### R3 — MLX LLM in the mesh (the desktop's compute contribution)
- Serve a local MLX/Metal model as an OpenAI-compatible endpoint, advertise it as
  rachel's `llm` resource, and let the `ModelRouter` prefer it for rachel's turns.
  Design: [`macos/docs/03-LLM-MESH.md`](../../../macos/docs/03-LLM-MESH.md).

## 4. Verify your node's access to everything shared
```sh
BLAZEN_INTEGRATION=1 BLAZEN_NODE=rachel make test-integration
```
The constellation integration suite
([`linux/agent/tests/integration/test_shared_resources.py`](../../../linux/agent/tests/integration/test_shared_resources.py))
is **self-strict, peer-lenient**: every resource rachel OWNS must be up; an offline
peer is tolerated. It probes llm/asr/tts/fabric/health/media + functional checks.
Run it on rachel to confirm rachel reaches paul's LLM/TTS/**media** and serves its
own fabric.

## 5. Coordination + invariants
- **Branch:** `main` is canonical (post-merge). Land small commits; `make test-fast`
  is the gate (it excludes the live integration suite). Keep cross-node logic in
  `domains/`, never copied into a surface.
- **Don't break the Pi or paul.** The mesh is strict-improvement — a node being off
  is never fatal to the others.
- **Reuse over rebuild:** `pull_catalog`/`published_catalog`/`serve_media`,
  `AudiobookProgress`, `context_sync`, `mesh_registry` are done — call them.
- **Secrets/models** stay in gitignored local files. Polish-first; PL+EN parity.

The finish line: talk to Jessica on the Mac, ask for a book, and she plays it from
paul's GPU renders — remembering what you told the Pi, in her one voice.
