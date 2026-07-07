"""`jessica` — text one-shot / REPL for the Jessica agent on a Linux node.

    BLAZEN_NODE=paul jessica "co potrafisz?"     # one-shot, prints the reply
    BLAZEN_NODE=paul jessica                      # interactive REPL

The heavy lifting is the shared `Assistant` engine; this is just the server-node
front door (no wake word). LLM/TTS/ASR come from the mesh (see node.py).
"""
from __future__ import annotations

import argparse
import os
from datetime import datetime
from pathlib import Path

from audiobook_catalog.progress import AudiobookProgress

from jessica_linux.fabric import pull_and_merge, serve
from jessica_linux.node import build_assistant
from jessica_linux.voice import Voice


def _node() -> str:
    return os.environ.get("BLAZEN_NODE") or "linux"


def _progress() -> AudiobookProgress:
    """Audiobook progress store in the shared library (syncs via the fabric)."""
    from jessica_linux.books import _LIBRARY
    return AudiobookProgress(path=str(_LIBRARY / "progress.json"))


def _context_paths(data: str | None) -> tuple[Path, Path]:
    """This node's memory.json + a sibling progress.json (shared-context stores)."""
    from blazend.domains.context.adapters.rpi5.memory import MemoryStore

    mem = MemoryStore(Path(data) if data else None).path
    return mem, mem.parent / "progress.json"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jessica", description="Jessica agent (Linux node).")
    ap.add_argument("prompt", nargs="?", help="one-shot: answer this utterance and exit")
    ap.add_argument("--data", help="memory JSON path (default: $BLAZEN_DATA_DIR/memory.json)")
    ap.add_argument("--speak", metavar="TEXT",
                    help="render+play this text via the node's mesh TTS (XTTS) and exit")
    ap.add_argument("--voice", action="store_true",
                    help="also speak replies aloud via the mesh TTS, not just print them")
    ap.add_argument("--sync", action="store_true",
                    help="pull peers' Jessica context from the mesh and merge it locally")
    ap.add_argument("--serve-fabric", action="store_true",
                    help="serve this node's context snapshot (:7475) so peers can pull it")
    ap.add_argument("--fleet", metavar="ACTION",
                    choices=["status", "start", "stop", "restart", "verify", "serve"],
                    help="manage this node's GPU service fleet (paul)")
    ap.add_argument("--read", metavar="QUERY",
                    help="read a Calibre ebook aloud via XTTS (rendered files if present, else live)")
    ap.add_argument("--ingest", metavar="QUERY",
                    help="render a Calibre ebook to kept MP3 chapters + the shared catalog (no playback)")
    ap.add_argument("--serve-media", action="store_true",
                    help="serve the audiobook library (:7477) so other nodes stream it")
    ap.add_argument("--pull-catalog", action="store_true",
                    help="merge peers' shared audiobook catalogs (mesh media) into this node's")
    ap.add_argument("--from-chunk", type=int, default=None,
                    help="resume --read/--ingest from this chapter/chunk (default: from saved progress)")
    args = ap.parse_args(argv)

    if args.fleet:
        from jessica_linux import fleet
        return fleet.cli(args.fleet, node=_node())

    if args.serve_media:
        from jessica_linux import books
        print("serving audiobook library on :7477 (Ctrl-C stops)")
        books.serve_media()
        return 0

    if args.pull_catalog:
        from jessica_linux import books
        cat = os.environ.get("BLAZEN_AUDIOBOOKS_CATALOG") or str(books._LIBRARY / "catalog.json")
        s = books.pull_catalog(node=_node(), local_catalog=cat)
        print(f"katalog: {s['books']} książek ({s['merged']} z sieci) → {cat}")
        return 0

    if args.ingest:
        from jessica_linux import books
        book = books.resolve(args.ingest)
        if book is None:
            print(f"nie znalazłam książki: {args.ingest!r}")
            return 1
        tts = Voice()
        if not tts.available:
            print("brak TTS w mesh — nie mogę renderować")
            return 1
        books.render_to_files(book, voice=tts, progress=_progress())
        return 0

    if args.read:
        from jessica_linux import books
        book = books.resolve(args.read)
        if book is None:
            print(f"nie znalazłam książki: {args.read!r}")
            return 1
        tts = Voice()
        if not tts.available:
            print("brak TTS w mesh — nie mogę czytać")
            return 1
        bookprog = _progress()
        saved = bookprog.get(book.slug)
        start = args.from_chunk if args.from_chunk is not None else ((saved["chapter"] + 1) if saved else 0)
        pre = books.rendered_chapters(book)
        print(f"Czytam „{book.title}” ({book.author}) od {start}"
              f"{' (z gotowych plików)' if pre else ''}…")
        if pre:  # prefer kept audio (chapter files) — resumable by chapter
            books.play_files([str(p) for p in pre], voice=tts, progress=bookprog,
                             slug=book.slug, title=book.title, start=start)
        else:  # render live, saving progress by chunk
            books.read(book, voice=tts, progress=bookprog, start=start)
        return 0

    # --speak is pure TTS: no LLM/agent needed.
    if args.speak is not None:
        Voice().speak(args.speak)
        return 0

    if args.serve_fabric:
        mem, prog = _context_paths(args.data)
        print(f"serving {_node()} context snapshot on :7475/fabric/snapshot (Ctrl-C stops)")
        serve(node=_node(), memory_path=mem, progress_path=prog)
        return 0

    if args.sync:
        mem, prog = _context_paths(args.data)
        s = pull_and_merge(node=_node(), memory_path=mem, progress_path=prog)
        print(f"synced: pulled {s['pulled'] or 'nobody'} — "
              f"{s['notes']} notes, {s['reminders']} reminders, {s['books']} books")
        return 0

    agent = build_assistant(data=Path(args.data) if args.data else None)
    voice = Voice() if args.voice else None

    def respond(text: str, *, prefix: str = "") -> None:
        if not text:
            return
        print(f"{prefix}{text}")
        if voice is not None:
            voice.speak(text)

    if args.prompt is not None:
        respond(agent.route(args.prompt, now=datetime.now()).text)
        return 0

    print("── Jessica (Linux node) ── Ctrl-D lub „wyjdź” kończy ──")
    while True:
        try:
            line = input("  ty> ")
        except EOFError:
            break
        if line.strip() in {"exit", "quit", "wyjdź", "wyjdz"}:
            break
        respond(agent.route(line, now=datetime.now()).text, prefix="  Jessica: ")
    print("\n  Jessica: Do usłyszenia!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
