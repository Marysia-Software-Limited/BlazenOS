#!/usr/bin/env python3
"""Index a music library into a flat JSON the appliance can search offline.

Walks a source tree, reads artist/title/album via ffprobe (falling back to the
filename / folder when tags are missing), and writes one record per track with
the path REWRITTEN to where the files live on the device. Jessica's
MusicDirectory loads this to resolve "zagraj <artist/title/album>" → a file path
(or a random track) for blazend-player to play locally.

Usage:
  scripts/index-music.py --src ~/Music --root /var/lib/blazen/music \
                         --out /var/lib/blazen/music-index.json
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

AUDIO_EXT = {".mp3", ".flac", ".m4a", ".ogg", ".opus", ".aac"}
_TRACK_PREFIX = re.compile(r"^\s*\d{1,3}\s*[-.\)]?\s+")  # "01 ", "01. ", "01-"

_PL_LETTERS = set("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ")
# What a broken round-trip produces: replacement chars and C1 controls.
_GARBAGE = {"�"} | {chr(i) for i in range(0x80, 0xA0)}


def demojibake(s: str) -> str:
    """Repair cp1250 tags mis-decoded as latin-1/cp1252 by old rippers —
    "Przekleñstwo" → "Przekleństwo", "Go\\x9ccie" → "Goście". The reverse
    round-trip is accepted only when it yields MORE Polish letters and no new
    garbage, so already-correct tags pass through untouched. Best-effort: tags
    mangled beyond these two encodings come back unchanged."""
    if not s or s.isascii():
        return s
    best = s
    for enc in ("latin-1", "cp1252"):
        try:
            fixed = s.encode(enc).decode("cp1250")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if _GARBAGE.isdisjoint(fixed) and (
                sum(c in _PL_LETTERS for c in fixed) > sum(c in _PL_LETTERS for c in best)):
            best = fixed
    return best


def probe_tags(path: Path) -> dict[str, str]:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format_tags=artist,album_artist,title,album,track,disc", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=15,
        )
        tags = (json.loads(out.stdout or "{}").get("format", {}) or {}).get("tags", {}) or {}
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        tags = {}
    # Repair mojibake; a tag ffprobe already lost bytes on (U+FFFD) is dropped
    # entirely so `derive` falls back to the (clean) filename/folder instead.
    return {k.lower(): demojibake(str(v).strip()) for k, v in tags.items()
            if "�" not in str(v)}


def _track_no(tags: dict[str, str], path: Path) -> tuple[int, int]:
    """(disc, track) for album ordering: the ID3 tag ("3" or "3/12"), falling
    back to a numbered filename ("03 …"). 0 = unknown."""
    def lead_int(s: str) -> int:
        m = re.match(r"\s*(\d{1,3})", s)
        return int(m.group(1)) if m else 0
    track = lead_int(tags.get("track", ""))
    if not track:
        m = _TRACK_PREFIX.match(path.stem)
        track = lead_int(m.group(0)) if m else 0
    return lead_int(tags.get("disc", "")), track


def derive(path: Path, src: Path, tags: dict[str, str]) -> dict[str, object]:
    stem = _TRACK_PREFIX.sub("", path.stem).strip()
    # "Artist - Title" in the filename is common when tags are missing.
    fn_artist, fn_title = "", stem
    if " - " in stem:
        a, t = stem.split(" - ", 1)
        fn_artist, fn_title = a.strip(), t.strip()
    rel_parts = path.relative_to(src).parts
    folder = rel_parts[-2] if len(rel_parts) >= 2 else ""
    title = tags.get("title") or fn_title
    album = tags.get("album") or folder
    artist = tags.get("artist") or tags.get("album_artist") or fn_artist
    # Folder as last-resort artist ONLY when it isn't just the album again — an
    # album-titled folder says nothing about who plays, and an "artist" equal to
    # the album name makes the resolver treat album requests as artist requests
    # (live 2026-07-27: dropped FFFD artist tags → artist="ballady mordercow" →
    # "zagraj ballady morderców" shuffled instead of playing in order).
    if not artist and folder != album:
        artist = folder
    disc, track = _track_no(tags, path)
    return {"title": title, "artist": artist, "album": album, "disc": disc, "track": track}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, default=Path.home() / "Music")
    ap.add_argument("--root", default="/var/lib/blazen/music",
                    help="where the files live on the device (path prefix in the index)")
    ap.add_argument("--out", type=Path, default=Path("music-index.json"))
    args = ap.parse_args()

    src = args.src.expanduser().resolve()
    files = sorted(p for p in src.rglob("*") if p.suffix.lower() in AUDIO_EXT)
    tracks = []
    for i, p in enumerate(files):
        meta = derive(p, src, probe_tags(p))
        meta["path"] = f"{args.root.rstrip('/')}/{p.relative_to(src).as_posix()}"
        tracks.append(meta)
        if i % 200 == 0:
            print(f"  indexed {i}/{len(files)}", file=sys.stderr)

    args.out.write_text(json.dumps({"version": 1, "tracks": tracks}, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    artists = sorted({t["artist"] for t in tracks if t["artist"]})
    print(f"indexed {len(tracks)} tracks, {len(artists)} artists → {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
