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

import os
import re
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_CALIBRE = Path(os.environ.get("CALIBRE_LIBRARY", str(Path.home() / "calibre")))
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


def epub_chapters(epub_path: Path) -> list[str]:
    """Chapter texts from an EPUB spine (HTML stripped), skipping tiny nav/cover docs."""
    import ebooklib  # noqa: PLC0415
    from bs4 import BeautifulSoup  # noqa: PLC0415
    from ebooklib import epub  # noqa: PLC0415

    book = epub.read_epub(str(epub_path))
    out: list[str] = []
    for item in book.get_items():
        if item.get_type() == ebooklib.ITEM_DOCUMENT:
            text = BeautifulSoup(item.get_content(), "html.parser").get_text("\n").strip()
            if len(text) > 200:
                out.append(re.sub(r"\n{3,}", "\n\n", text))
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
            from datetime import UTC, datetime  # noqa: PLC0415
            progress.save(book.slug, chapter=i, offset_s=0.0, title=book.title,
                          updated=datetime.now(UTC).isoformat())
        if prefetch is not None:
            prefetch.join()
    return done
