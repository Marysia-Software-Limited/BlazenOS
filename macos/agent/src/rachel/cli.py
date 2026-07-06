"""rachel-audiobook — render Calibre ebooks to Polish audiobooks and play them.

Subcommands:
- ``list``   — every Polish book in the Calibre library (slug, author, title, formats).
- ``render`` — extract a book, render chapters with Apple TTS (or Azure ``--premium``),
               write the shared catalog entry. Starts `afplay` on chapter 1 as a
               smoke unless ``--no-play`` (the real player lands in Phase B).
- ``play``   — resolve a rendered book in the Mac catalog and play it via
               ``rachel-player`` (the shared ``domains/blazend-audiobook`` engine),
               resuming from saved progress and auto-advancing chapters.
- ``resume`` — continue the most-recently-played book.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from audiobook_catalog.directory import AudiobookDirectory
from audiobook_catalog.progress import AudiobookProgress

from rachel import paths, player, secrets
from rachel.calibre import CalibreLibrary
from rachel.extract import epub_to_chapters, text_to_chapters
from rachel.ingest import render_book, upsert_catalog_entry
from rachel.tts import AppleTTS, AzureTTS, installed_polish_voice_is_compact

_CALIBRE = os.environ.get("CALIBRE_LIBRARY", str(Path.home() / "calibre"))


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _library() -> CalibreLibrary:
    return CalibreLibrary(db_path=str(Path(_CALIBRE) / "metadata.db"), library_root=_CALIBRE)


def _chapters_for(book, lib):
    epub = book.format_file(lib.library_root, "EPUB")
    if epub:
        return epub_to_chapters(str(epub))
    src = next((p for f in book.formats
                if (p := book.format_file(lib.library_root, f))), None)
    if not src:
        raise SystemExit(f"no readable format for {book.slug}")
    txt = Path(src).with_suffix(".rachel.txt")
    subprocess.run(["ebook-convert", str(src), str(txt)], check=True, capture_output=True)
    try:
        chs = text_to_chapters(txt.read_text(encoding="utf-8", errors="ignore"))
    finally:
        txt.unlink(missing_ok=True)
    return chs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="rachel-audiobook")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    r = sub.add_parser("render")
    r.add_argument("query", nargs="*")
    r.add_argument("--id", type=int)
    r.add_argument("--premium", action="store_true")
    r.add_argument("--voice", default="Zosia")
    r.add_argument("--no-play", action="store_true")
    p = sub.add_parser("play")
    p.add_argument("query", nargs="+")
    sub.add_parser("resume")
    a = ap.parse_args(argv)
    lib = _library()

    if a.cmd == "list":
        for b in lib.polish_books():
            print(f"{b.slug:14} {b.author} — {b.title}  [{','.join(b.formats)}]")
        return 0

    if a.cmd == "render":
        book = next((x for x in lib.polish_books() if x.id == a.id), None) if a.id \
            else lib.resolve(" ".join(a.query))
        if not book:
            print("book not found", file=sys.stderr)
            return 1
        if a.premium:
            s = secrets.load()
            key = s.get("AZURE_SPEECH_KEY")
            if not key:
                print("--premium needs AZURE_SPEECH_KEY in macos/.secrets.env", file=sys.stderr)
                return 1
            tts = AzureTTS(key=key, region=s.get("AZURE_REGION", "westeurope"))
        else:
            if installed_polish_voice_is_compact(a.voice):
                print(f"note: only the compact '{a.voice}' voice is installed — for "
                      "audiobook quality download the Premium/Enhanced voice in System "
                      "Settings → Accessibility → Spoken Content → System Voices.",
                      file=sys.stderr)
            tts = AppleTTS(voice=a.voice)
        chapters = _chapters_for(book, lib)
        print(f"rendering {book.slug} ({book.title}): {len(chapters)} chapters…",
              file=sys.stderr)
        started = {"done": False}

        def _kick(index: int, mp3: Path) -> None:
            if not started["done"] and not a.premium and not a.no_play:
                started["done"] = True
                subprocess.Popen(["afplay", str(mp3)])  # smoke playback until Phase B player

        entry = render_book(book, chapters, tts, root=paths.audiobook_root(),
                            premium=a.premium, voice=a.voice, on_chapter=_kick)
        upsert_catalog_entry(paths.catalog_path(), entry)
        print(f"rendered {entry['slug']}: {entry['n_chapters']} ch → {paths.catalog_path()}")
        return 0

    if a.cmd in ("play", "resume"):
        directory = AudiobookDirectory(catalog_path=str(paths.catalog_path()))
        progress = AudiobookProgress(path=str(paths.progress_path()))
        if a.cmd == "play":
            book = directory.resolve(" ".join(a.query))
            if not book:
                print("book not found in the rendered catalog — render it first",
                      file=sys.stderr)
                return 1
            slug = book.slug
        else:  # resume
            slug = player.latest_slug(str(paths.progress_path()))
            book = directory.by_slug(slug) if slug else None
            if not book:
                print("nothing to resume", file=sys.stderr)
                return 1
        saved = progress.get(slug) or {}
        print(f"playing {slug} ({book.title}) from chapter {int(saved.get('chapter', 0))}",
              file=sys.stderr)
        player.play_book(list(book.chapters), slug, book.title,
                        progress=progress, now=_now(),
                        start_chapter=int(saved.get("chapter", 0)),
                        start_seconds=float(saved.get("offset_s", 0.0)))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
