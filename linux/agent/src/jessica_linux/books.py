"""Read a Calibre ebook aloud on a Linux node: EPUB → chapters → XTTS → player.

Resolves a book in the local Calibre library (``~/calibre/metadata.db``) by title,
extracts chapter text from its EPUB (ebooklib + BeautifulSoup), chunks it to
TTS-sized pieces, and renders each via the node's mesh XTTS + plays it through
``blazend-player`` — prefetching the next chunk's audio while the current one plays,
so playback is continuous. Progress (chunk index) is saved so it resumes.

`ebooklib` + `beautifulsoup4` are extras (``pip install -e linux/agent[calibre]``),
imported lazily so the rest of the agent stays dependency-light.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import threading
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_CALIBRE = Path(os.environ.get("CALIBRE_LIBRARY", str(Path.home() / "calibre")))
# Where rendered audiobooks + the shared catalog live (served to other nodes).
_LIBRARY = Path(os.environ.get("BLAZEN_AUDIOBOOKS_DIR", str(Path.home() / "audiobooks")))
_MAX_CHARS = 700  # per-chunk TTS budget (a few sentences → snappy render, no truncation)


@dataclass
class Book:
    book_id: int
    title: str
    author: str
    epub: Path
    slug: str


def resolve(query: str, *, library: Path | None = None) -> Book | None:
    """First Calibre book whose title matches ``query`` (with an EPUB)."""
    lib = library or _CALIBRE
    con = sqlite3.connect(str(lib / "metadata.db"))
    try:
        rows = con.execute(
            "SELECT id, title, author_sort, path FROM books WHERE title LIKE ? ORDER BY title LIMIT 1",
            (f"%{query}%",),
        ).fetchall()
    finally:
        con.close()
    if not rows:
        return None
    bid, title, author, path = rows[0]
    epubs = sorted((lib / path).glob("*.epub"))
    if not epubs:
        return None
    return Book(book_id=int(bid), title=str(title), author=str(author or ""),
                epub=epubs[0], slug=f"calibre-{bid}")


# Invisible / zero-width characters to delete outright (soft hyphen, ZWSP/ZWNJ/ZWJ, BOM).
_INVISIBLE = dict.fromkeys(map(ord, "­​‌‍﻿"), None)


def clean_text(text: str) -> str:
    """Normalise extracted book text for TTS — strip artifacts XTTS would mis-read.

    Removes zero-width/soft-hyphen chars, normalises odd spaces, de-hyphenates
    words split across line breaks, folds the single line breaks the HTML extractor
    injects mid-paragraph into spaces (keeping blank-line paragraph breaks), drops
    editorial ellipsis markers ``(...)``/``[…]`` and standalone page-number lines.
    """
    t = unicodedata.normalize("NFC", text).translate(_INVISIBLE)
    t = re.sub(r"[    \t]+", " ", t)          # nbsp/thin/tab → space
    t = re.sub(r"(\w)-\n(\w)", r"\1\2", t)                         # de-hyphenate wy-\nraz → wyraz
    t = re.sub(r"[\(\[]\s*(?:\.{2,}|…)\s*[\)\]]", " ", t)          # (...) / […] editorial marks
    t = re.sub(r"(?<!\n)\n(?!\n)", " ", t)                         # join single line breaks FIRST
    t = re.sub(r"(?m)^\s*\d{1,4}\s*$", "", t)                      # then drop true page-number lines
    t = re.sub(r"[ ]{2,}", " ", t)                                 # collapse runs of spaces
    t = re.sub(r"\n[ ]+", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)                               # >2 blank lines → paragraph
    return t.strip()


def epub_chapters(epub_path: Path) -> list[str]:
    """Cleaned chapter texts from an EPUB spine (HTML stripped), skipping tiny docs."""
    import ebooklib  # noqa: PLC0415
    from bs4 import BeautifulSoup  # noqa: PLC0415
    from ebooklib import epub  # noqa: PLC0415

    book = epub.read_epub(str(epub_path))
    out: list[str] = []
    for item in book.get_items():
        if item.get_type() == ebooklib.ITEM_DOCUMENT:
            raw = BeautifulSoup(item.get_content(), "html.parser").get_text("\n")
            text = clean_text(raw)
            if len(text) > 200:
                out.append(text)
    return out


def chunks(chapters: list[str], *, max_chars: int = _MAX_CHARS) -> list[str]:
    """Split chapters into <= max_chars pieces on paragraph then sentence boundaries."""
    out: list[str] = []
    for chapter in chapters:
        buf = ""
        for para in (p.strip() for p in re.split(r"\n\s*\n", chapter) if p.strip()):
            if buf and len(buf) + len(para) + 2 > max_chars:
                out.append(buf)
                buf = ""
            buf = f"{buf}\n\n{para}" if buf else para
            while len(buf) > max_chars:  # a single over-long paragraph → sentence-split
                cut = buf.rfind(". ", 0, max_chars)
                cut = cut + 1 if cut > max_chars // 2 else max_chars
                out.append(buf[:cut].strip())
                buf = buf[cut:].strip()
        if buf:
            out.append(buf)
    return out


def read(book: Book, *, voice: Any, device: str | None = None, progress: Any = None,
         start: int = 0, log: Callable[[str], None] = print) -> int:
    """Read ``book`` aloud from chunk ``start``, rendering the next chunk while the
    current one plays. Returns the number of chunks read."""
    parts = chunks(epub_chapters(book.epub))
    total = len(parts)
    log(f"„{book.title}” — {book.author}: {total} fragmentów (od {start})")

    rendered: dict[int, bytes] = {}

    def render_into(i: int) -> None:
        try:
            rendered[i] = voice.render(parts[i])
        except Exception as e:  # noqa: BLE001 — a failed render skips that chunk
            rendered[i] = b""
            log(f"  render {i} failed: {e}")

    render_into(start)
    done = 0
    for i in range(start, total):
        wav = rendered.pop(i, b"")
        prefetch = None
        if i + 1 < total:
            prefetch = threading.Thread(target=render_into, args=(i + 1,), daemon=True)
            prefetch.start()
        if wav:
            voice.play_wav(wav, device=device)
            done += 1
        if progress is not None:
            progress.save(book.slug, chapter=i, offset_s=0.0, title=book.title,
                          updated=datetime.now(UTC).isoformat())
        if prefetch is not None:
            prefetch.join()
    return done


def rendered_chapters(book: Book, *, library: Path | None = None) -> list[Path]:
    """Already-rendered chapter MP3s for a book (sorted), or []."""
    bdir = (library or _LIBRARY) / book.slug
    return sorted(bdir.glob("[0-9]*.mp3")) if bdir.exists() else []


def _concat_to_mp3(wavs: list[Path], out_mp3: Path) -> None:
    """Concatenate chunk WAVs into one chapter MP3 via ffmpeg."""
    listfile = out_mp3.with_suffix(".concat.txt")
    listfile.write_text("".join(f"file '{w.as_posix()}'\n" for w in wavs), encoding="utf-8")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listfile),
             "-codec:a", "libmp3lame", "-qscale:a", "4", str(out_mp3)],
            check=True, capture_output=True,
        )
    finally:
        listfile.unlink(missing_ok=True)


def upsert_catalog(book: Book, chapters: list[str], *, library: Path | None = None) -> Path:
    """Add/replace this book in the shared catalog.json. Chapters are stored
    slug-relative (``<slug>/NNN.mp3``) so any node resolves them against its own
    library dir or the mesh ``media`` URL."""
    lib = library or _LIBRARY
    lib.mkdir(parents=True, exist_ok=True)
    cat_path = lib / "catalog.json"
    try:
        cat = json.loads(cat_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cat = {"version": 1, "books": []}
    rel = [f"{book.slug}/{Path(c).name}" for c in chapters]
    entry = {"slug": book.slug, "title": book.title, "author": book.author,
             "source": "calibre-tts", "language": "pl", "downloaded": True,
             "n_chapters": len(rel), "chapters": rel}
    cat["books"] = [b for b in cat.get("books", []) if b.get("slug") != book.slug] + [entry]
    tmp = cat_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cat, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(cat_path)
    return cat_path


def render_to_files(book: Book, *, voice: Any, library: Path | None = None,
                    progress: Any = None, log: Callable[[str], None] = print) -> list[str]:
    """Render `book` to persistent per-chapter MP3s under ``library/<slug>/`` and
    keep the shared catalog current. Resumable — chapters already on disk are
    skipped, so it can be stopped and restarted. Returns the chapter MP3 paths."""
    lib = library or _LIBRARY
    bdir = lib / book.slug
    bdir.mkdir(parents=True, exist_ok=True)
    chapters = epub_chapters(book.epub)
    total = len(chapters)
    log(f"„{book.title}” — {book.author}: {total} rozdziałów → {bdir}")
    out: list[str] = []
    for ci, chapter in enumerate(chapters):
        mp3 = bdir / f"{ci + 1:03d}.mp3"
        if mp3.exists() and mp3.stat().st_size > 1000:
            out.append(str(mp3))
            continue
        wavs: list[Path] = []
        for j, part in enumerate(chunks([chapter])):
            wf = bdir / f".part_{ci + 1:03d}_{j:03d}.wav"
            wf.write_bytes(voice.render(part))
            wavs.append(wf)
        if wavs:
            _concat_to_mp3(wavs, mp3)
            for wf in wavs:
                wf.unlink(missing_ok=True)
            out.append(str(mp3))
        log(f"  rozdział {ci + 1}/{total} → {mp3.name}")
        upsert_catalog(book, out, library=lib)  # keep the catalog current as it renders
        if progress is not None:
            progress.save(book.slug, chapter=ci, offset_s=0.0, title=book.title,
                          updated=datetime.now(UTC).isoformat())
    upsert_catalog(book, out, library=lib)
    log(f"gotowe: {len(out)} rozdziałów w {bdir}")
    return out


def play_files(chapters: list[str], *, voice: Any, device: str | None = None,
               progress: Any = None, slug: str = "", title: str = "",
               start: int = 0, log: Callable[[str], None] = print) -> int:
    """Play already-rendered chapter files/URLs via blazend-player, saving progress
    per chapter (resume). Returns the number of chapters played."""
    total = len(chapters)
    for i in range(start, total):
        log(f"  rozdział {i + 1}/{total}")
        voice.play_file(chapters[i], device=device)
        if progress is not None:
            progress.save(slug, chapter=i, offset_s=0.0, title=title,
                          updated=datetime.now(UTC).isoformat())
    return max(0, total - start)


def serve_media(*, library: Path | None = None, host: str = "0.0.0.0", port: int = 7477) -> None:
    """Serve the audiobook library over HTTP so other nodes stream chapters + the
    catalog (``GET /<slug>/NNN.mp3``, ``/catalog.json``). Advertised as the paul
    ``media`` mesh resource. Blocks (Ctrl-C stops)."""
    import functools  # noqa: PLC0415
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer  # noqa: PLC0415

    lib = library or _LIBRARY
    lib.mkdir(parents=True, exist_ok=True)
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(lib))
    ThreadingHTTPServer((host, port), handler).serve_forever()
