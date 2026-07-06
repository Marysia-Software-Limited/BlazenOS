# 04 — Calibre → Polish TTS ingest (rachel owns this)

**Moved here from the Pi's audiobook plan** (the ebooks + the Mac are the user's,
so rachel is the natural ingest node). rachel turns the user's Calibre ebooks into
Polish **audiobooks** and feeds them into the **shared** catalog so the whole
constellation — especially the `jessica` appliance — can play them through the
audiobook engine that already exists.

## What exists to plug into (don't rebuild)
The Pi session already built the **audiobook playback engine**: chapter
auto-advance, remember/restore position (`--start-seconds` + `--position-file` in
`blazend-player`), resume-aware `audiobook_play`, `AudiobookProgress`. A book is
just a `catalog.json` entry with `{author, title, slug, chapters:[mp3…],
n_chapters, downloaded, genre, epoch}` (see
[`../../scripts/fetch-wolnelektury.py`](../../scripts/fetch-wolnelektury.py) for the
schema and [`../../scripts/build-ontology.py`](../../scripts/build-ontology.py) /
[`../../scripts/build-semantic-index.py`](../../scripts/build-semantic-index.py) for
the RAG). **A Calibre book that lands in `catalog.json` with rendered chapter MP3s
gets chapters, resume, recommendations, and the attention-check for free.**

## The pipeline (rachel-side, on-demand per book)
1. **Extract** — read `~/calibre/metadata.db` (sqlite: id/title/author/language/
   formats). For a requested book, get text: EPUB via `ebooklib` (spine → chapters),
   or `ebook-convert <book>.epub/.mobi/.pdf out.txt` for the rest. Split into
   chapters (EPUB spine items or heading breaks). **Filter to Polish** (`language =
   pol`) to start. Slug = `calibre-<id>` (stable).
2. **Render — Azure Neural (pl-PL) now; ElevenLabs later for selected books.**
   Azure Speech SDK/REST, a `pl-PL` neural voice (e.g. `pl-PL-MarekNeural`, from a
   config), SSML for sentence pacing; each chapter → an MP3.
   **Progressive:** render chapter 1 first and start playback while chapters 2..N
   render in the background (rendering is faster than real-time, so it stays ahead).
   Cost control: **on-demand per book** (a whole book is ~0.5–1 M chars ≈ $8–15 on
   Azure) — never bulk-render all ~5505. Key `AZURE_SPEECH_KEY` in a local secrets
   file (never git, never chat). Later: an ElevenLabs path for a hand-picked premium
   shelf (much pricier per book), selected by the user.
3. **Ingest into the SHARED library** — write the chapters to
   `/var/lib/blazen/audiobooks/<slug>/NN.mp3`, append the book to `catalog.json`
   with `source:"calibre-tts", language:"pl"`, **rsync to `jessica` (the Pi)** and
   **`chown blazen`** (see [[music-owned-by-blazen]] — media must be readable by the
   blazen service user, or playback silently fails). Rebuild the semantic index
   (`build-semantic-index.py`) so it shows up in recommendations. If rachel keeps a
   local copy, it can also read it directly.
4. **Voice** — "przeczytaj [tytuł]" resolves across the **merged** catalog: a Wolne
   Lektury audiobook → play; a Calibre ebook not yet rendered → kick off the render
   and start ch 1 as it lands.

## Where the code goes (as built, 2026-07-06)
- rachel's agent: `macos/agent/src/rachel/` (Python) — `calibre.py` (read
  `~/calibre/metadata.db`, filter `pol`, extract chapters), `tts.py`
  (`AppleTTS` default via `say`→ffmpeg; `AzureTTS` opt-in), `ingest.py`
  (progressive render + `catalog.json` upsert), `player.py` + `cli.py`
  (`rachel-audiobook list|render|play|resume`). Deps `ebooklib`/`bs4` and the
  optional Azure SDK are **rachel dev deps**, never shipped to the Pi.
- Shared libs under `domains/` (domains for common code): `audiobook-catalog`
  (Python catalog/resolver/progress, imported by both rachel and the Pi) and
  `blazend-audiobook` (Rust playback engine behind `AudioSink`; rachel's
  `macos/player/rachel-player` links a cpal sink).
- **TTS decision:** Apple on-device is the **default** renderer (Zosia, Apple
  Silicon); Azure Neural (`pl-PL-MarekNeural`) is the **`--premium` opt-in** for a
  hand-picked shelf, key in the gitignored `macos/.secrets.env`. (This supersedes
  the "Azure now" framing above — Apple acceleration is the default per the user's
  2026-07-06 direction.)
- Mac-local first: rendered books land in
  `~/Library/Application Support/blazen/audiobooks/<slug>/` in the shared schema;
  rsync/chown to the Pi's `/var/lib/blazen/audiobooks/` is a later phase.
- No change to the Pi's playback contract — it already plays anything in the catalog.

## On-device invariant
Cloud TTS (Azure/ElevenLabs) is a deliberate **opt-in exception** for the reading
feature and runs at **ingest/render time only** (on rachel/paul). Pi runtime
playback stays 100 % local MP3. Same pattern as the DSPy-compiled-on-paul prompts
and the gpt-5.5 opt-in.

## Build order
1. Extract one Polish EPUB → chapter texts (verify chapter splitting).
2. Render ch 1 via Azure → MP3; confirm pl-PL quality + tokens/cost.
3. Ingest → `catalog.json` + rsync/chown to the Pi → "przeczytaj [tytuł]" plays it
   with full resume/auto-advance.
4. Progressive background render of remaining chapters.
5. (Later) ElevenLabs path + a "premium" flag for selected books.
