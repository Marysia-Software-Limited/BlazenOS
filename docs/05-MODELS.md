# 05 — Models

All models run on-device on the Raspberry Pi 5. The default path is
**CPU only** — the Pi 5 VideoCore doesn't help for our chosen runtimes.
An optional Hailo accelerator (see [`12-ML-ACCELERATOR.md`](12-ML-ACCELERATOR.md))
speeds up the LLM (and optionally ASR) without changing observable
behaviour.

> **Decision (2026-06-11):** **No cloud models** in the default path. Optional
> cloud-backed tools (e.g., a weather plugin that hits a public API) are
> allowed but opt-in and disabled in release builds.

> **Decision (2026-06-11):** Pi 5 **16 GB** is the reference. All tables
> below are pinned to it. Pi 5 8 GB remains supported with the smaller
> ASR / LLM defaults; Pi 5 4 GB is best-effort. Older Pi 4/3 numbers
> have been removed; see `git log -- docs/05-MODELS.md` for the
> pre-pivot revision.

## Wake word — openWakeWord

| Field        | Value                                    |
|--------------|------------------------------------------|
| Backend      | ONNX Runtime (CPU)                       |
| Default model| `hey_blazen.onnx` (custom)               |
| Size         | ~4 MB                                    |
| RAM          | ~80 MB resident                          |
| Pi 5 latency | ~15 ms per 80 ms window                  |
| Pi 4 latency | ~25 ms per 80 ms window                  |
| Training set | 4000 synthetic Piper utterances + 50 real |
| License      | Apache-2.0                               |

Custom wake words are trained from scratch via the openWakeWord
recipe in `scripts/train-wake-word.py` (M6 milestone).

## ASR — faster-whisper (CTranslate2)

| Variant          | Size (INT8) | Pi 5 5-s latency | RAM     | Pi 5 16 GB default? | Pi 5 8 GB default? |
|------------------|------------:|-----------------:|--------:|---------------------|---------------------|
| `tiny.en`        | 75 MB       | 300 ms           | 400 MB  | Fallback            | Fallback            |
| `base.en`        | 142 MB      | 500 ms           | 600 MB  | —                   | Constrained dev     |
| `small.en`       | 466 MB      | 700 ms           | 1200 MB | —                   | EN-only flavour     |
| `small` (multi)  | 466 MB      | 750 ms           | 1200 MB | —                   | **Yes**             |
| `medium` (multi) | 1.5 GB      | 1.6 s            | 2700 MB | **Yes**             | Opt-in              |
| `large-v3-turbo` | 1.6 GB      | 1.8 s            | 3000 MB | Opt-in              | Opt-in              |

> **Decision (2026-06-11):** on Pi 5 **16 GB**, ship the multilingual
> `medium` as the default — better PL diacritics and still well within
> the latency budget. Pi 5 8 GB keeps `small` (multilingual) so we don't
> crowd the LLM. `small.en` remains available for the `lang_mode: en_only`
> build flavour and as a low-RAM fallback. Polish-quality footguns and
> the recommended `large-v3-turbo` upgrade are documented in
> [`13-LANGUAGES.md`](13-LANGUAGES.md) §5. A Hailo Whisper-base path is
> **experimental** (~250 ms 5-s latency on Hailo-8); see
> [`12-ML-ACCELERATOR.md`](12-ML-ACCELERATOR.md) §4.

License: MIT (Whisper) / MIT (CTranslate2).

## VAD — silero-vad

| Field   | Value           |
|---------|-----------------|
| Size    | 2 MB ONNX       |
| RAM     | 30 MB           |
| License | MIT             |

## LLM — llama.cpp (CPU) / HailoRT (optional)

We pick from this table. Sizes are Q4_K_M GGUF for the CPU path; Q4_0 is
smaller but quality drops noticeably for 1.5–3B models. Hailo columns
apply only when an AI HAT+ is installed and a matching `.hef` is
available; otherwise the engine selector falls back to CPU
automatically. See [`12-ML-ACCELERATOR.md`](12-ML-ACCELERATOR.md).

| Model                          | Size  | Pi 5 CPU TTFT | Pi 5 CPU tok/s | Hailo-10H TTFT | Hailo-10H tok/s | 16 GB default | 8 GB default |
|--------------------------------|------:|--------------:|---------------:|---------------:|----------------:|---------------|--------------|
| **Bielik-4.5B-v3.0-Instruct-Q6_K** | 3.9 GB | 2700 ms | **2.2** (measured) | TBD | TBD | **Yes** | **Yes (PL-first default)** |
| Bielik-1.5B-v3.0-Instruct-Q4_K_M | 1.0 GB | 650 ms | **9.6** (measured) | — | n/a | Alt | **Snappy fallback** |
| Bielik-4.5B-v3.0-Instruct-Q4_K_M | 2.9 GB | ~450 ms | ~3 (est) | — | TBD | Alt | Alt (smaller, faster than Q6_K) |
| Qwen2.5-1.5B-Instruct-Q4_K_M   | 1.0 GB | 300 ms       | 18             | n/a (rare)     | n/a             | —             | Low-RAM fallback |
| Qwen2.5-3B-Instruct-Q4_K_M     | 2.0 GB | 350 ms       | 12             | ~120 ms        | ~35             | Alt           | Alt |
| Qwen2.5-7B-Instruct-Q4_K_M     | 4.5 GB | 650 ms       | 6              | ~250 ms        | ~18             | Opt-in (16 GB only) | — |
| Llama-3.2-3B-Instruct-Q4_K_M   | 2.0 GB | 400 ms       | 10             | ~140 ms        | ~32             | Alt           | Alt |
| Phi-3.5-mini-Instruct-Q4_K_M   | 2.4 GB | 500 ms       | 9              | ~180 ms        | ~28             | Experimental  | Experimental |
| TinyLlama-1.1B-Q4_K_M          | 700 MB | 200 ms       | 22             | n/a            | n/a             | Smoke tests only | Smoke tests only |

> **Decision (2026-06-22): switched the default to Bielik** (SpeakLeash,
> Polish-first). Bielik v3-small is *built on Qwen2.5*, so it keeps Qwen's
> English while lifting Polish sharply — Open PL LLM Leaderboard (Instruct,
> 5-shot): **Bielik-4.5B 56.1 / Bielik-1.5B 41.4 vs Qwen2.5-3B 41.2**.
>
> **Update (2026-06-24, measured on a real Pi 5 8 GB, no swap): the default
> is Bielik-4.5B-v3.0 Q6_K** (the richer-Polish brain). Empirically it fits
> in 8 GB with **~3 GB headroom** alongside Whisper-small + Piper (mmap,
> `use_mlock: false`; the `VmHWM` peak reads ~7.3 GB but that over-counts
> reclaimable mmap page-cache — real working set ≈ 3.9 GB). The catch is
> **speed: ~2.2 tok/s on CPU** (multi-second replies) — slowness is
> compute-bound, so 16 GB doesn't fix it; the **Hailo path** is the route to
> a big-and-fast brain. **Bielik-1.5B-v3.0 Q4_K_M is the snappy fallback**
> (**9.6 tok/s**, ~2 GB, fluent casual Polish) via *"użyj mniejszego mózgu" /
> "use the smaller brain"*. GGUFs come from `second-state/…-GGUF` (the
> speakleash repos ship only fp16/Q8). Small-model caveat: a 1.5B can
> slip on facts (it called Kraków the capital) — factual/complex questions
> escalate to Gemini per the routing rules. Qwen 2.5 stays in the catalogue
> as an alternate; the 11B Bielik (Polish leaderboard champion, ~6.7 GB Q4)
> needs 16 GB. Hailo figures are vendor reference, re-measured at M9.
>
> **(Prior, 2026-06-11):** Qwen 2.5 3B was the default; superseded above.

Licenses: Bielik v3 Apache-2.0; Qwen2.5 Apache-2.0; Llama 3.2 Llama
Community License; Phi-3.5 MIT; TinyLlama Apache-2.0.

### Context window

- Default `n_ctx = 4096`. Conversation history is summarised when it
  exceeds 3072 tokens.
- KV cache stays in RAM; we do not page to disk.

### Task-based routing (the brain's `ModelRouter`)

The brain does **not** talk to one fixed model. A `ModelRouter`
(`ai_orchestrator/core/model_router.py`, config in
[`configs/llm.yaml`](../configs/llm.yaml) `routing:`) sends each request to the
cheapest capable backend and falls back gracefully:

| Task | Order (first *available* wins) |
|------|--------------------------------|
| `command`  (quick replies) | **Bielik 1.5B** (on-device) |
| `recommend` (book/music RAG reasoning) | **Bielik 1.5B** (on-device) |
| `open_qa` (advanced science / open questions) | **Bielik 1.5B** (on-device) |

- **Decision 2026-07-13 (node-local processing):** every task runs on the Pi
  itself — no mesh hop, no cloud in the command path. The backend catalogue in
  `llm.yaml` retains the mesh/cloud entries (rachel's `mlx-*`, `gpt-5.5`) so
  re-enabling is a one-line task-list edit.
- **Decision 2026-08-09 (user request):** `ollama-11b` (paul's GPU Bielik) is
  **removed** from the Pi's `llm.yaml` backend catalogue and from the Pi's
  `/etc/blazen/mesh.yaml` resources — the Pi can never route to paul's LLM.
  Paul's own agent still resolves its local Ollama via the repo `mesh.yaml`
  entry (see the note there).
- On the 8 GB Pi only **one** local Bielik is resident at a time
  (`single_local_model: true` → the router calls `LocalLlm.close()` to evict the
  other). The 16 GB reference Pi fits both.
- **GPT-5.5** (OpenAI) is in the catalogue but routed to no task; when routed,
  it requires a key in `/etc/blazen/secrets.env`. News stays on Gemini
  search-grounding (real web research, not a chat guess).

### Book/music recommendations — multi-layer RAG

`assistant/recommend.py` layers **semantic** (e5 over the
`semantic-index.{npy,json}`) → **metadata** (catalogue genre/epoch) → **ontology**
([`configs/ontology/books.json`](../configs/ontology/books.json): genre/epoch
synonyms + a curated author→nationality table, built by
`scripts/build-ontology.py`). A nationality ask ("francuska klasyka") hard-prefers
matching authors and diversifies by author. The chosen candidate + spoken pitch
come from a **DSPy-compiled prompt**: signatures are optimised **offline on paul**
by `scripts/compile-prompts.py` (BootstrapFewShot, Ollama 11B teacher) and shipped
as static JSON under [`configs/prompts/`](../configs/prompts/) — **the Pi imports
no `dspy`**, it just fills the prompt (`assistant/prompts.py`). See
[`14-RUST-PYTHON-SPLIT.md`](14-RUST-PYTHON-SPLIT.md).

### System prompt (default)

The authoritative prompt lives in [`configs/llm.yaml`](../configs/llm.yaml)
`system_prompt:` — keep this excerpt in sync with it. Since the Jessica
rebrand the assistant identifies as **Jessica** (casual: Jess; Polish
vocative: Jessico); the code-name `blazen` is a developer term only and is
never spoken to the user.

```
You are Jessica (casual: Jess; Polish vocative: Jessico), a voice-first
personal assistant. You run on a Raspberry Pi 5 in the user's home.
The user hears you through a speaker; they cannot see a screen.

LANGUAGE: reply in the same language the user used (Polish or English).
If you can't tell, default to Polish (the primary user is Polish-only).
Keep replies short — one or two sentences unless the user asks for detail.

HONESTY: never invent tool outputs, source citations, or facts.
ROUTING: answer simple factual questions locally; escalate complex /
up-to-date / "deep research" questions to Gemini.
IDENTITY: you are Jessica everywhere. Code-names like "blazen" or
"rachel" are developer terms — never use them with the user.
PRIVACY: read back cloud calls / stored data honestly; never lie about
whether something went to the cloud.
```

## TTS — Piper

| Voice                       | Size  | Pi 5 first-audio latency | Default for |
|-----------------------------|------:|-------------------------:|-------------|
| `pl_PL-darkman-medium`      | 50 MB | 90 ms                    | Polish (primary default) |
| `en_US-lessac-medium`       | 50 MB | 80 ms                    | English |
| `en_US-amy-medium`          | 50 MB | 80 ms                    | English alt |
| `en_GB-alan-low`            | 30 MB | 60 ms                    | Low-RAM fallback |

License: MIT.

## Model download flow

`make models` reads each `configs/*.yaml`, finds entries with
`download_url + sha256`, and pulls into `./models/`. SHA mismatch → error,
no overwrite. On the device, the image build copies `models/` to
`/var/lib/blazen/models/` (on the `data` partition).

## Storage budget

| Tier                            | Footprint | Fits 32 GB? | Fits 64 GB? | Fits 128 GB? |
|---------------------------------|----------:|:-----------:|:-----------:|:-----------:|
| OS + blazend (Python + Rust)    | ~2.0 GB   | ✓           | ✓           | ✓           |
| Default Pi 5 16 GB bundle       | ~4.0 GB   | tight       | ✓           | ✓           |
| Default Pi 5 8 GB bundle        | ~2.5 GB   | ✓           | ✓           | ✓           |
| Bundle + Hailo .hef             | +2.5 GB   | no          | ✓           | ✓           |
| Bundle + Qwen 2.5 7B upgrade    | +4.5 GB   | no          | tight       | ✓           |
| All ASR variants pre-downloaded | ~4 GB     | tight       | ✓           | ✓           |
| All LLM variants pre-downloaded | ~12 GB    | no          | tight       | ✓           |
| Soak-run logs (24h)             | ~500 MB   | ✓           | ✓           | ✓           |

> **Decision (2026-06-11):** release images ship only the platform-default
> models. Others download lazily on the first voice command that needs them
> (with audible feedback: "downloading the Polish voice, one moment").
