#!/usr/bin/env python3
"""Build the on-device semantic index over the local music + audiobook library.

Embeds every track and book (title/artist/album, author) with the baked e5
embedder into a vector matrix + a parallel metadata list, so Jessica can answer
"znajdź coś spokojnego" / "muzyka o miłości" / "książka o morzu" by meaning, not
just exact names. Runs ON THE DEVICE (needs the embedder + numpy). Notes/emails
can be folded into the same index later.

Outputs:  <out>.npy   (float32 [N, dim], L2-normalised)
          <out>.json  ({"dim":…, "items":[{type,title,who,path|chapters}, …]})

Usage (on the Pi):
  BLAZEN_MODELS_DIR=/var/lib/blazen/models PYTHONPATH=/usr/lib/blazen \
    .venv/bin/python scripts/build-semantic-index.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from blazend.domains.context.adapters.rpi5.embeddings import Embedder


def load_items(music: Path, books: Path) -> list[dict]:
    items: list[dict] = []
    try:
        for t in json.loads(music.read_text(encoding="utf-8")).get("tracks", []):
            who = t.get("artist", "")
            text = f"Utwór „{t.get('title','')}”. Wykonawca: {who}. Album: {t.get('album','')}."
            items.append({"type": "music", "title": t.get("title", ""), "who": who,
                          "path": t.get("path", ""), "text": text})
    except (OSError, ValueError):
        pass
    try:
        for b in json.loads(books.read_text(encoding="utf-8")).get("books", []):
            who = b.get("author", "")
            extra = ". ".join(x for x in (
                f"Gatunek: {b.get('genre','')}" if b.get("genre") else "",
                f"Epoka: {b.get('epoch','')}" if b.get("epoch") else "") if x)
            text = f"Audiobook, książka „{b.get('title','')}”. Autor: {who}." + (f" {extra}." if extra else "")
            items.append({"type": "book", "title": b.get("title", ""), "who": who,
                          "genre": b.get("genre", ""), "epoch": b.get("epoch", ""),
                          "downloaded": bool(b.get("downloaded", bool(b.get("chapters")))),
                          "chapters": b.get("chapters", []), "text": text})
    except (OSError, ValueError):
        pass
    return items


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--music", type=Path, default=Path("/var/lib/blazen/music-index.json"))
    ap.add_argument("--books", type=Path, default=Path("/var/lib/blazen/audiobooks/catalog.json"))
    ap.add_argument("--out", type=Path, default=Path("/var/lib/blazen/semantic-index"))
    args = ap.parse_args()

    emb = Embedder()
    if not emb.available:
        print("embedder unavailable — cannot build semantic index", file=sys.stderr)
        return 1
    items = load_items(args.music, args.books)
    if not items:
        print("no music/book items to index", file=sys.stderr)
        return 1

    vecs: list[list[float]] = []
    for i in range(0, len(items), 128):
        batch = [it["text"] for it in items[i : i + 128]]
        vecs.extend(emb.embed(batch, kind="passage"))
        print(f"  embedded {min(i + 128, len(items))}/{len(items)}", file=sys.stderr)

    mat = np.asarray(vecs, dtype=np.float32)
    np.save(str(args.out) + ".npy", mat)
    meta = [{k: it[k] for k in it if k != "text"} for it in items]
    Path(str(args.out) + ".json").write_text(
        json.dumps({"dim": mat.shape[1], "items": meta}, ensure_ascii=False), encoding="utf-8")
    print(f"semantic index: {mat.shape[0]} items × {mat.shape[1]}d → {args.out}.npy/.json", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
