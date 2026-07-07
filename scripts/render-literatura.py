#!/usr/bin/env python3
"""Long-term batch: render every Calibre `literatura` book without audio to a
shared XTTS audiobook.

Finds books tagged **literatura** (a Calibre tag) that have an EPUB and are not
yet rendered, and renders each — one at a time — via the node's mesh XTTS into the
shared audiobook library (`$BLAZEN_AUDIOBOOKS_DIR`, default `~/audiobooks`) +
`catalog.json`, which `jessica --serve-media` shares to the other nodes.

Designed for the long haul (315 books ≈ many GPU-hours):
- **Resumable** at two levels — a per-book manifest (`render-literatura.json`)
  skips finished books, and `books.render_to_files` skips chapters already on
  disk, so it's safe to stop/restart (or run as a systemd service) anytime.
- One book at a time (XTTS is the single GPU consumer).
- A failed book is recorded and skipped; the batch keeps going.

    BLAZEN_NODE=paul scripts/render-literatura.py [--tag literatura] [--limit N]
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

from jessica_linux import books
from jessica_linux.voice import Voice


def tagged_books(tag: str, library: Path) -> list[books.Book]:
    """Calibre books carrying `tag` that have an EPUB, as reader Book objects."""
    con = sqlite3.connect(str(library / "metadata.db"))
    try:
        rows = con.execute(
            """SELECT b.id, b.title, b.author_sort, b.path
               FROM books b
               JOIN books_tags_link btl ON btl.book = b.id
               JOIN tags t ON t.id = btl.tag
               WHERE t.name = ? ORDER BY b.title""",
            (tag,),
        ).fetchall()
    finally:
        con.close()
    out: list[books.Book] = []
    for bid, title, author, path in rows:
        epubs = sorted((library / path).glob("*.epub"))
        if epubs:
            out.append(books.Book(book_id=int(bid), title=str(title), author=str(author or ""),
                                  epub=epubs[0], slug=f"calibre-{bid}"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Batch-render a Calibre tag to shared audiobooks.")
    ap.add_argument("--tag", default="literatura", help="Calibre tag to render (default: literatura)")
    ap.add_argument("--limit", type=int, default=0, help="max books this run (0 = all)")
    args = ap.parse_args()

    calibre = Path(os.environ.get("CALIBRE_LIBRARY", str(Path.home() / "calibre")))
    lib = books._LIBRARY
    lib.mkdir(parents=True, exist_ok=True)
    manifest_path = lib / "render-literatura.json"
    try:
        manifest: dict[str, dict] = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = {}

    voice = Voice()
    if not voice.available:
        print("brak TTS w mesh — nie mogę renderować", file=sys.stderr)
        return 1

    def flush() -> None:
        tmp = manifest_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(manifest_path)

    all_books = tagged_books(args.tag, calibre)
    pending = [b for b in all_books if manifest.get(b.slug, {}).get("status") != "done"]
    print(f"'{args.tag}': {len(all_books)} książek z EPUB, {len(pending)} do zrobienia "
          f"→ {lib}", flush=True)

    done = 0
    for book in pending:
        if args.limit and done >= args.limit:
            break
        print(f"[{done + 1}/{len(pending)}] „{book.title}” ({book.slug})", flush=True)
        manifest.setdefault(book.slug, {}).update(
            title=book.title, status="rendering", updated=datetime.now(UTC).isoformat())
        flush()
        try:
            chapters = books.render_to_files(book, voice=voice)
            manifest[book.slug].update(status="done", chapters=len(chapters),
                                       updated=datetime.now(UTC).isoformat())
        except Exception as e:  # noqa: BLE001 — record + skip a bad book, keep the batch alive
            manifest[book.slug].update(status="failed", error=str(e)[:200],
                                       updated=datetime.now(UTC).isoformat())
            print(f"  BŁĄD: {e}", file=sys.stderr, flush=True)
        flush()
        done += 1

    finished = sum(1 for v in manifest.values() if v.get("status") == "done")
    print(f"sesja: {done} zrobione teraz | łącznie gotowych: {finished}/{len(all_books)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
