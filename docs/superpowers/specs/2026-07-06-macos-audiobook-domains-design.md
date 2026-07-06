# Design — rachel (macOS) Calibre → Apple-TTS audiobooks, domains-first

**Date:** 2026-07-06
**Node:** rachel (macOS, Apple Silicon) — session on host `rebeca`
**Branch:** `refactor/domain-architecture` (shared with the `jessica`/Pi + `paul`/Linux sessions)
**Status:** approved design; implementing Phases A + B now, Phase C additively on the shared branch.

> **Coordination note for the `paul` and `jessica` sessions:** this design **moves
> device-independent audiobook functionality out of the `rpi5/` adapter into shared
> `domains/` common libs** so every node (jessica, rachel, and future) links one
> source of truth instead of copying. Two new common libs are introduced:
> `domains/audiobook-catalog/` (Python — the **first Python package under `domains/`**)
> and `domains/blazend-audiobook/` (Rust). The Pi keeps working unchanged; the Pi's
> `blazend-player` is rewired onto the shared Rust core additively (Phase C), keeping
> `make test-fast` / the appliance build green throughout.

---

## 1. Goal

From the Mac, turn a Polish **Calibre** ebook (`~/calibre`) into a chapterized
**audiobook** rendered with **Apple on-device TTS**, cataloged in the existing
shared schema, and playable on the Mac with **resume + chapter auto-advance** —
by **reusing** the Pi's audiobook engine (via new shared `domains/` libs), not
rebuilding it. Mac-local first; Azure premium opt-in; Pi sync deferred.

This is the "focus on audiobook" slice of the broader rachel node
(`macos/docs/00-CONTEXT.md`). The MLX LLM mesh and Apple Speech ASR remain
**deferred** — see Non-goals.

## 2. Decisions (locked with the user)

| Decision | Choice |
|---|---|
| TTS engine | **Apple on-device default + Azure premium opt-in** (per-book `--premium` flag) |
| First deliverable | **Calibre → audiobook pipeline** (extract → Apple render → catalog → play) |
| Library location | **Mac-local first**, sync to the Pi shared catalog deferred |
| Mac playback | **Reuse the Rust `blazend-player`** (exact-parity seek/position-file), not `afplay` |
| Common code | **Always `domains/` for common code** — extract shared libs; no cross-adapter copy |
| Pi rewire (Phase C) | Done **additively on this branch, Pi kept green**, flagged for the jessica session |
| Build scope now | **Phases A + B** (render pipeline + Rust Mac player); Phase C after |

## 3. Architecture

### 3.1 New common libs under `domains/`

`domains/` is the shared-core workspace (its own `domains/Cargo.toml`); `rpi5/`
depends on it one-directionally and never the reverse. Two libs are added:

| Lib | Lang | Extracted from | Linked by |
|---|---|---|---|
| `domains/audiobook-catalog/` | **Python** (new; first Python pkg in `domains/`) | `rpi5/.../assistant/audiobooks.py` + `audiobook_progress.py` | rpi5 agent + macos agent |
| `domains/blazend-audiobook/` | **Rust** lib crate | portable guts of `rpi5/voice-output/blazend-player` | Pi player + Mac player |

**`domains/audiobook-catalog/` (Python).** Holds the device-independent audiobook
logic that currently lives inside the rpi5 adapter:
- the `catalog.json` model — `{version, books:[{author,title,slug,genre,epoch,downloaded,chapters:[paths],n_chapters, ...}]}`
- `AudiobookDirectory` — accent-folded/stemmed spoken-query → `Book` resolver
- `AudiobookProgress` — per-`slug` `{chapter, offset_s, title, updated}` store, atomic save
- the small Polish fold/stem helpers the resolver needs (`_fold`, `_stem_phrase`),
  moved with it (today they live in `assistant/radio.py`) or a minimal copy if
  `radio.py` carries unrelated deps — pick the lighter dependency footprint.
- Both `BLAZEN_AUDIOBOOKS_CATALOG` / `BLAZEN_AUDIOBOOK_PROGRESS` env overrides keep
  working, so a node points them at its own paths.
- Packaged as an installable Python package (`pyproject.toml`) so `rpi5/` and
  `macos/agent/` both depend on it (editable install / path dep). rpi5's
  `assistant/audiobooks.py` + `audiobook_progress.py` become thin re-export shims
  (or their imports are repointed) so **Pi runtime behavior is byte-for-byte the same**.

**`domains/blazend-audiobook/` (Rust).** The **portable player engine**, extracted
from `blazend-player`:
- buffered decode (via `symphonia`), **seek to `--start-seconds`**, **`--position-file`**
  progress writing, **chapter auto-advance** across an ordered MP3 list.
- output goes through an **`AudioSink` trait** — the core contains **no ALSA**. The
  platform picks the sink: ALSA on Linux (Pi), CoreAudio/`cpal` on macOS.
- pure engine + trait → unit-testable without a sound device.

### 3.2 Platform adapters

- **Pi (`rpi5/`)** — `blazend-player` becomes a thin binary that links
  `domains/blazend-audiobook` + an **ALSA `AudioSink`**. CLI/flags unchanged
  (`--start-seconds`, `--position-file`). Phase C; additive; Pi stays green.
- **Mac (`macos/`)**:
  - `macos/agent/` (Python package `rachel`) — the ingest/render brain:
    - `rachel/calibre.py` — read `~/calibre/metadata.db` (sqlite: id/title/author/
      language/formats), filter `language = pol`, resolve a requested book, extract
      text → chapters. EPUB via `ebooklib` (spine → chapters); fallback
      `ebook-convert <book> out.txt` + heading split. Slug = `calibre-<id>` (stable).
    - `rachel/tts.py` — `TtsBackend` protocol `render_chapter(text, out_path)`.
      - `AppleTTS` (default): `say -v <voice> -o ch.aiff` → `ffmpeg`/`lame` → `NN.mp3`.
        On-device Apple-Silicon neural synthesis. Warns when only the **compact**
        Zosia voice is installed (recommend downloading **Zosia Premium** in
        System Settings → Accessibility → Spoken Content → System Voices).
      - `AzureTTS` (opt-in, `--premium`): `pl-PL-MarekNeural` via the Azure Speech
        SDK/REST; SSML sentence pacing; key `AZURE_SPEECH_KEY` from the gitignored
        `macos/.secrets.env`. Never bulk-render.
    - `rachel/ingest.py` — orchestrate extract → **progressive** render (chapter 1
      first, start playback, render 2..N in background — rendering beats real-time) →
      write MP3s to the Mac-local root → append/update the catalog entry
      (`source:"calibre-tts", language:"pl", voice, premium`) via
      `domains/audiobook-catalog`.
    - `rachel/cli.py` — `rachel-audiobook list | render <query|id> | play <query> | resume`.
    - depends on `domains/audiobook-catalog`.
  - `macos/player/` (Rust) — thin `rachel-player` binary: `domains/blazend-audiobook`
    core + a **CoreAudio/`cpal` `AudioSink`**. This is the "reuse the Rust player" path.

Mac-local audiobook root: `~/Library/Application Support/blazen/audiobooks/<slug>/NN.mp3`.
Local `catalog.json` + `progress.json` alongside it.

### 3.3 Data flow

```
~/calibre/metadata.db ──(calibre.py: filter pol, resolve, extract)──► chapter texts
      │
      ▼ (tts.py: AppleTTS default / AzureTTS --premium, progressive)
   NN.mp3 per chapter  ──(ingest.py)──►  catalog.json entry  (domains/audiobook-catalog)
      │
      ▼ (rachel-player: domains/blazend-audiobook core + CoreAudio sink)
   playback with seek + auto-advance  ──►  progress.json  (resume next run)
```

## 4. Phasing (each phase independently testable; Pi green throughout)

- **Phase A — catalog lib + Mac render pipeline.**
  1. Create `domains/audiobook-catalog/` (Python); move the resolver + progress +
     fold/stem there; repoint rpi5 imports; prove Pi unit tests still green.
  2. Build `macos/agent/rachel` render path: `rachel-audiobook render "<title>"` →
     Apple-TTS chapter MP3s + a valid catalog entry (`calibre-<id>`).
  - **DoD:** render succeeds on a real Polish Calibre book; unit tests green; a
    manual `afplay` smoke confirms audible Polish.

- **Phase B — Rust player on the Mac.**
  1. Extract `domains/blazend-audiobook/` (portable engine + `AudioSink` trait) from
     `blazend-player`'s guts.
  2. `macos/player/` (`rachel-player`) with a CoreAudio/`cpal` sink.
  3. `rachel-audiobook play/resume` drives it with seek + auto-advance → `progress.json`.
  - **DoD:** play a rendered book on the Mac with resume + chapter auto-advance,
    matching the Pi's flag semantics.

- **Phase C — rewire the Pi's `blazend-player`** onto `domains/blazend-audiobook`
  (jessica-session-coordinated; ALSA sink; CLI unchanged; Pi build + `make test-fast`
  stay green).

## 5. Testing

- **Unit (no audio):** chapter splitting on a fixture EPUB; catalog entry schema +
  `slug` stability; `TtsBackend` command construction for both backends with a
  subprocess stub; `AudiobookProgress` round-trip; resolver match cases.
- **Rust:** `domains/blazend-audiobook` engine tests with a mock `AudioSink`
  (seek offset, auto-advance across a chapter list, position-file writes) — no device.
- **rpi5 regression:** existing audiobook unit tests must stay green after the
  Python extraction (proves the Pi is unchanged).
- **Manual smoke:** render ch1 of one Polish book → play on the Mac → stop → resume.

## 6. Invariants honored

- **Polish-first:** filter `language = pol`; pl-PL Zosia voice; Polish leads.
- **On-device default, cloud opt-in:** Apple TTS is the default; Azure only behind
  `--premium`; `AZURE_SPEECH_KEY` in gitignored `macos/.secrets.env`, never committed
  or pasted into chat. Same opt-in pattern as the appliance's gpt-5.5 / paul-compiled prompts.
- **No models/secrets committed.** MP3 outputs and `~/calibre` are not committed.
- **Reuse, not rebuild:** the Pi's engine becomes shared `domains/` libs; no
  cross-adapter copy-paste. **Always `domains/` for common code.**
- **Don't break `jessica`:** the Pi appliance never depends on rachel; the Pi
  player rewire is additive and kept green.
- **PL/EN asset parity:** this is a PL ingest of the user's own Polish ebooks; EN
  parity is **N/A for this feature** and documented here (there is no EN counterpart
  to a user's Polish ebook). Any assistant-facing phrasing added later keeps PL+EN.

## 7. Non-goals (deferred)

MLX LLM mesh; Apple Speech ASR; Swift menu-bar UI; Pi rsync/`chown` sync of rendered
books to the shared catalog; bulk-render-all; ElevenLabs. Each is a later rachel phase.

## 8. Coordination / risk

The `domains/audiobook-catalog` extraction and the `domains/blazend-audiobook`
extraction + Pi `blazend-player` rewire touch code the `jessica`/`paul` sessions
also work on (shared branch `refactor/domain-architecture`). Mitigations: additive
moves, thin re-export shims so the Pi is byte-for-byte unchanged, Pi tests kept
green as the gate, small commits, and this spec committed up-front so the other
sessions see the domains move before it lands.
