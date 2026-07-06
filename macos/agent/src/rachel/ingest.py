"""Orchestrate extract → progressive TTS render → shared catalog entry.

``render_book`` renders chapters one at a time (progressive: a caller can start
playback on the first chapter via ``on_chapter`` while the rest render), writes
each ``NN.mp3`` under ``root/<slug>/``, and returns a catalog entry in the shared
schema. ``upsert_catalog_entry`` load-or-creates ``catalog.json`` and replaces
any existing entry with the same slug (atomic write).
"""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from rachel.calibre import CalibreBook
from rachel.extract import Chapter
from rachel.tts import TtsBackend


def upsert_catalog_entry(catalog_path: Path, entry: dict) -> None:
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {"version": 1, "books": []}
    books = [b for b in data.get("books", []) if b.get("slug") != entry.get("slug")]
    books.append(entry)
    data = {"version": 1, "books": books}
    tmp = catalog_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(catalog_path)


def render_book(book: CalibreBook, chapters: list[Chapter], tts: TtsBackend, *,
                root: Path, premium: bool = False, voice: str = "Zosia",
                on_chapter: Callable[[int, Path], None] | None = None) -> dict:
    """Render every chapter to ``root/<slug>/NN.mp3`` and return the catalog entry."""
    slug = book.slug
    book_dir = Path(root) / slug
    paths: list[str] = []
    for ch in chapters:
        mp3 = book_dir / f"{ch.index:02d}.mp3"
        tts.render_chapter(ch.text, mp3)
        paths.append(str(mp3))
        if on_chapter:
            on_chapter(ch.index, mp3)
    return {
        "author": book.author,
        "title": book.title,
        "slug": slug,
        "genre": "",
        "epoch": "",
        "downloaded": True,
        "chapters": paths,
        "n_chapters": len(paths),
        "source": "calibre-tts",
        "language": "pl",
        "voice": voice,
        "premium": premium,
    }


__all__ = ["render_book", "upsert_catalog_entry"]
