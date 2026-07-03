#!/usr/bin/env python3
"""Download Polish public-domain audiobooks from Wolne Lektury for offline play.

Uses the Wolne Lektury API (https://wolnelektury.pl/api/), downloads the MP3
chapters of each audiobook into  <out>/<Author>/<Title>/NN_<chapter>.mp3  with
ASCII-safe filenames (Polish diacritics transliterated, ł→l ż→z …) so any player
handles them, and writes a catalog.json the TTS can read to a blind user.

Re-runnable: skips books/chapters already present. Bound the run with --limit
(books) and/or --max-gb.

Usage:
  scripts/fetch-wolnelektury.py --out ~/audiobooks --limit 40 --max-gb 15
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
import urllib.request
from pathlib import Path

API_LIST = "https://wolnelektury.pl/api/audiobooks/?format=json"
_PL = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")


def ascii_safe(text: str, *, fallback: str = "x") -> str:
    """Transliterate Polish + strip to a filesystem/ASCII-safe token."""
    t = unicodedata.normalize("NFKC", text).translate(_PL)
    t = "".join(c if c.isascii() else "-" for c in t)
    t = re.sub(r"[^A-Za-z0-9._-]+", "_", t).strip("_.")
    return t or fallback


def get_json(url: str) -> object:
    with urllib.request.urlopen(url, timeout=30) as r:  # noqa: S310 (fixed host)
        return json.loads(r.read().decode("utf-8"))


def download(url: str, dest: Path) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=120) as r, tmp.open("wb") as f:  # noqa: S310
        n = 0
        while chunk := r.read(1 << 16):
            f.write(chunk)
            n += len(chunk)
    tmp.replace(dest)
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path.home() / "audiobooks")
    ap.add_argument("--root", default="/var/lib/blazen/audiobooks",
                    help="on-device path prefix written into catalog.json")
    ap.add_argument("--limit", type=int, default=0, help="max books (0 = all)")
    ap.add_argument("--max-gb", type=float, default=0.0, help="stop after ~this many GB (0 = no cap)")
    ap.add_argument("--catalog", type=Path, default=None)
    args = ap.parse_args()

    out = args.out.expanduser()
    out.mkdir(parents=True, exist_ok=True)
    books = get_json(API_LIST)
    print(f"{len(books)} audiobooks listed", file=sys.stderr)

    catalog, total = [], 0
    budget = int(args.max_gb * (1 << 30)) if args.max_gb else 0
    for i, meta in enumerate(books):
        if args.limit and len(catalog) >= args.limit:
            break
        if budget and total >= budget:
            print(f"reached --max-gb ({args.max_gb}) — stopping", file=sys.stderr)
            break
        try:
            detail = get_json(meta["href"] + "?format=json")
        except Exception as e:  # noqa: BLE001
            print(f"  skip {meta.get('slug')}: {e}", file=sys.stderr)
            continue
        author = meta.get("author") or detail.get("author") or "Nieznany"
        title = meta.get("title") or detail.get("title") or meta.get("slug", "")
        mp3s = [m["url"] for m in detail.get("media", []) if m.get("type") == "mp3"]
        if not mp3s:
            continue
        bdir = out / ascii_safe(author) / ascii_safe(title)
        chapters = []
        for n, url in enumerate(sorted(mp3s), 1):
            name = ascii_safe(Path(url).stem)[-60:] or f"ch{n:03d}"
            dest = bdir / f"{n:03d}_{name}.mp3"
            rel = dest.relative_to(out).as_posix()
            if not dest.exists():
                try:
                    total += download(url, dest)
                except Exception as e:  # noqa: BLE001
                    print(f"  chapter fail {url}: {e}", file=sys.stderr)
                    continue
            chapters.append(f"{args.root.rstrip('/')}/{rel}")
        if chapters:
            catalog.append({"author": author, "title": title, "slug": meta.get("slug", ""),
                            "chapters": chapters, "n_chapters": len(chapters)})
            print(f"  [{len(catalog)}] {author} — {title} ({len(chapters)} ch, {total/1e9:.1f} GB)",
                  file=sys.stderr)

    cat_path = args.catalog or (out / "catalog.json")
    cat_path.write_text(json.dumps({"version": 1, "books": catalog}, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    print(f"catalog: {len(catalog)} books → {cat_path} ({total/1e9:.1f} GB)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
