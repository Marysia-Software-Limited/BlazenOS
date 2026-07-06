# Handoff — build the **paul** node toward *full Jessica everywhere*

> **FOR THE `paul` CLAUDE SESSION (Linux, RTX 3090).** Written by the `rachel`
> (macOS) session that just: shipped the Calibre→audiobook pipeline, extracted
> the audiobook engine into shared `domains/` libs, stood up **XTTS-v2 on paul**
> as a systemd GPU service, and built **Phase 1 of the node mesh** (a shared
> resource registry). Your job: turn paul from a passive *service host* into a
> full **Jessica node**, and drive the constellation toward the target the user
> stated verbatim: **"full Jessica everywhere."**

Read this whole doc first, then the linked ones. Everything is on branch
`refactor/domain-architecture` (you are on `fix/post-merge-integration` — see
§Coordination; reconcile before you start).

---

## 1. The target: full Jessica everywhere

One **Jessica** — same personality, same memory/context, same skills — reachable
on **every node**, each contributing and using the best resources on the LAN:

| Node | Machine | Role in the constellation |
|------|---------|---------------------------|
| **jessica** | Raspberry Pi 5 | always-on **voice appliance** (reference node; full agent today) |
| **rachel** | MacBook (Apple Silicon) | **desktop** Jessica; Apple TTS + Calibre audiobooks (partial today) |
| **paul** | Linux + RTX 3090 | **GPU/heavy-lift** node; **your job to make it a full node** |

"Full Jessica everywhere" = two planes working across all three:
- **Resource plane** — nodes advertise LLM/ASR/TTS; any node uses the best one on
  the mesh (paul's Ollama-11B + XTTS + Whisper are the heavy hitters).
- **Context/personality plane** — memory (notes, reminders, profile, book/track
  progress) + Jessica's identity **replicate** across nodes, so she's the *same*
  Jessica whether you talk to the Pi, the Mac, or paul.

Hard invariants (from root `AGENTS.md`/`CLAUDE.md`) still hold: Polish-first
runtime, on-device by default (cloud/GPU is a render/offload-time opt-in), the Pi
must stay fully functional standalone (the mesh is strict-improvement, never a
dependency), **domains/ for all common code**, PL+EN asset parity.

---

## 2. What already exists (don't rebuild — plug into these)

**Resource mesh — Phase 1 (NEW, mine).**
- [`configs/mesh.yaml`](../../../configs/mesh.yaml) — the registry: every node +
  the resources it advertises. **paul is already in it** with `ollama-11b`,
  `whisper-remote`, and `xtts`:
  ```yaml
  paul:
    role: gpu
    host: 192.168.50.102
    resources:
      llm: { ollama-11b: { kind: openai, url: "http://192.168.50.102:11434", model: bielik-11b } }
      asr: { whisper-remote: { kind: faster-whisper, url: "http://192.168.50.102:8090/transcribe", model: large-v3 } }
      tts: { xtts: { kind: xtts, url: "http://192.168.50.102:8091/synthesize", language: pl, speaker: "Ana Florence" } }
  ```
- [`domains/mesh-registry/`](../../../domains/mesh-registry/) — the shared loader.
  API: `Mesh.load()` → `.resource("tts","xtts")`, `.resources("llm")`,
  `.host("paul")`, `.self_node` (from `$BLAZEN_NODE`). Reads `$BLAZEN_MESH` or
  `/etc/blazen/mesh.yaml`, else the repo `configs/mesh.yaml`. 5 tests.
- **Reference consumer:** rachel's `--engine xtts` resolves paul's XTTS endpoint
  from the mesh, not a constant (see `macos/agent/src/rachel/cli.py`). Do the same
  for the Pi/paul's LLM + ASR (they still hardcode `192.168.50.102:11434` / `:8090`).

**XTTS GPU service (NEW, mine) — this is "paul manages XTTS."**
- [`scripts/xtts_server.py`](../../../scripts/xtts_server.py) — Coqui XTTS-v2,
  POST `/synthesize {text,language,speaker}` → WAV. Voice-clone via
  `BLAZEN_XTTS_SPEAKER_WAV`.
- [`scripts/blazen-xtts.service`](../../../scripts/blazen-xtts.service) — installed
  + enabled on paul now (`systemctl status blazen-xtts`, port 8091, healthy on cuda).
- [`scripts/xtts-requirements.txt`](../../../scripts/xtts-requirements.txt) — the
  **verified** dep pins (coqui-tts 0.27.5 + transformers 4.57.6 + torchcodec; the
  combo is fragile — don't let it drift). venv: `~/dev/blazen_os/.venv-xtts` (py3.12).
- Chosen voice: XTTS built-in **"Ana Florence"** speaking Polish (user A/B'd it vs
  Apple Enhanced vs AWS Polly and vs cloned Wolne-Lektury narrators; picked this).

**The Jessica agent (Pi) — your starting stack.** `rpi5/src/blazend/` is mostly
portable Python: `ModelRouter` ([`.../ai_orchestrator/core/model_router.py`](../../../rpi5/src/blazend/domains/ai_orchestrator/core/model_router.py)),
`prompts.py` (DSPy-compiled, no dspy at runtime), `tools.py`, the book/music RAG,
`MemoryStore`. paul is Linux like the Pi → **reuse this**, swapping adapters for
paul's resources (Ollama-11B local, XTTS, Whisper).

**Audiobook domain libs (NEW, mine)** — `domains/audiobook-catalog` (Python:
catalog/resolver/progress) and `domains/blazend-audiobook` (Rust: portable player
engine behind an `AudioSink` trait). See the companion handoff
[`2026-07-06-phase-c-pi-player-rewire-HANDOFF.md`](2026-07-06-phase-c-pi-player-rewire-HANDOFF.md)
(Phase C: rewire the Pi's `blazend-player` onto the shared Rust engine — that's
*also* yours, since it needs a Linux/ALSA build you can do and I can't on macOS).

**Context plane substrate.** [`domains/blazend-fabric`](../../../domains/blazend-fabric/)
(Rust, minimal) + [`configs/fabric.yaml`](../../../configs/fabric.yaml). This is
where shared context/personality will live — currently a stub; you grow it.

---

## 3. Phased plan for the paul node (do in order; each is testable)

### P0 — paul joins the mesh as itself (small; do first)
- `export BLAZEN_NODE=paul`. Confirm `Mesh.load().self_node == "paul"` and that
  paul can enumerate every node's resources.
- Sanity: from paul, hit its own advertised endpoints via the registry (Ollama,
  Whisper, XTTS) — prove discovery works end-to-end locally.
- **DoD:** a one-liner that prints paul's resources + reaches each.

### P1 — Run the Jessica agent on paul (talk to her on paul)
- Stand up the Pi's Python agent on paul (new surface — recommend `linux/` or
  `paul/` mirroring `macos/agent/`, OR a thin runner that imports `rpi5/src/blazend`
  + the `domains/` libs; decide and document). No wake word / kiosk — a **TUI or
  local socket** is the interface (paul is a server).
- Wire its adapters to paul's resources **via the mesh**: LLM → `ollama-11b`
  (local, fast), TTS → `xtts`, ASR → `whisper-remote`. Reuse `prompts.py` + the RAG
  verbatim (same compiled prompts as the Pi — Polish quality parity).
- **DoD:** `<paul>/ai "co potrafisz?"` (or a REPL) answers in Polish via Ollama-11B,
  and "przeczytaj [tytuł]" can render+play through XTTS. Pi + rachel unaffected.

### P2 — Shared context + personality (`blazend-fabric`) — the heart of "one Jessica"
- Design the sync payload: memory notes, reminders, user profile/name, and
  per-book/-track progress (the same `AudiobookProgress` slugs rachel/Pi write).
- v1: **read-mostly** — paul pulls a shared snapshot from the Pi over the fabric;
  prove a note saved on the Pi is recalled on paul (and vice-versa).
- v2: **append-only SyncLog** both directions; last-writer-wins per key → CRDT if
  needed. Grow `domains/blazend-fabric` (it's a stub today).
- Personality: Jessica's identity/system-persona is shared config, not per-node —
  make sure all nodes load the *same* persona so she's one character everywhere.
- **DoD:** save a memory on any node → recalled on the other two; same name, same
  persona, same book progress across nodes.

### P3 — Generalize routing onto the mesh (kill the hardcoded IPs)
- Make the `ModelRouter` (and the ASR client) consume `mesh.yaml` like rachel's TTS
  does: resolve `ollama-11b` / `whisper-remote` from the registry instead of the
  constants baked into `blazend-brain.service` / `blazend-asr.service` /
  `ollama.py` / `asr/engine.py`. Keep the **policy** (task→backend order) in
  `llm.yaml`; the registry only supplies the **where**.
- Add **self-first + reachability probes**: each node prefers its nearest/fastest
  resource, falls back across the mesh, and degrades gracefully when a peer is off
  (the Pi must never hard-depend on paul).
- **DoD:** unit tests mirroring `rpi5/tests/unit/test_model_router.py` for multi-node
  ordering/fallback; flip paul off → Pi + rachel still answer locally.

### P4 — paul manages its GPU resource fleet
- paul owns the lifecycle of the shared GPU services (XTTS, Ollama, Whisper):
  systemd health, model warm/cold, VRAM budgeting on the 24 GB 3090 (Ollama-11B
  ~10 GB + Whisper ~3 GB + XTTS ~3 GB — fits, but coordinate concurrency).
- Expose a tiny `/health` aggregation + surface it in the mesh (a node liveness bit)
  so other nodes route around a down service.
- **DoD:** `make`-style targets to start/stop/verify paul's service fleet; other
  nodes route correctly when one is down.

---

## 4. The mesh contract (how everything plugs in)

- **Registry file:** `configs/mesh.yaml` — `version`, `self`, `nodes.<name>.{role,host,resources.{llm,asr,tts}.<id>.{kind,url,model,local,...}}`.
- **Loader:** `from mesh_registry import Mesh` → `Mesh.load(path=None)`; overrides:
  `$BLAZEN_NODE` (who am I), `$BLAZEN_MESH` (registry path).
- **Add a resource:** edit `mesh.yaml` under the owning node; consumers resolve it
  by `(category, id)`. **Don't** add new hardcoded peer URLs anywhere else.
- **Boundary:** discovery only. LLM task routing policy stays in `configs/llm.yaml`.

---

## 5. Coordination (read before you push)

- **Branches:** all my work is on `refactor/domain-architecture` (also merged to
  `main`, tip `db251bd` at merge time; more has landed since). **You are on
  `fix/post-merge-integration`.** Reconcile: rebase/merge so you have the mesh
  registry + audiobook domain libs + XTTS before extending them. Coordinate with
  the user on which branch is canonical.
- **Domains for common code** — the mesh loader, audiobook libs, and fabric are
  shared; new cross-node logic goes in `domains/`, never copied into a surface.
- **Don't break the Pi or rachel.** `make test-fast` is the gate (lint + Tier 0/1,
  both Rust workspaces + Python). Note: there's a **pre-existing
  `test_model_router` failure** on the branch (LLM-config drift) — it's yours to
  fix as part of P3, and it's unrelated to my audiobook/mesh work.
- **Secrets** stay in gitignored local files (never commit keys/models).
- **Two "node" senses** (the user asked): *Jessica runtime nodes* (this doc) vs
  *Claude dev sessions* (you on paul, me on the Mac). They map 1:1 per machine but
  are different: dev sessions coordinate via **git**; runtime nodes via the **mesh
  + fabric**. Build the runtime; keep coordinating over git.

---

## 6. First moves when you pick this up
1. Reconcile your branch with `refactor/domain-architecture` (§5).
2. `cd ~/dev/blazen_os && systemctl status blazen-xtts` — confirm XTTS is healthy;
   `curl -s localhost:8091/health`.
3. Do **P0** (mesh membership) — smallest real win; proves paul sees the constellation.
4. Read `macos/docs/03-LLM-MESH.md` (the mesh design) + `docs/19-DOMAIN-ARCHITECTURE.md`
   (where code lives) before P1.
5. Then P1 (agent on paul) → P2 (fabric/shared context — the core of "one Jessica").

The finish line: talk to Jessica on paul, and she knows what you told the Pi, in
her one voice, using paul's GPU when it helps and standing alone when it doesn't.
