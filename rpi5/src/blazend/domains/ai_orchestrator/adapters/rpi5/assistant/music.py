"""Offline music directory — resolve a spoken request to a local track.

Backs "zagraj Kazika" / "zagraj coś" (random). The catalogue is a flat JSON index
built by ``scripts/index-music.py`` (artist/title/album + on-device path), loaded
from ``$BLAZEN_MUSIC_INDEX`` or ``/var/lib/blazen/music-index.json``. Pure resolve
logic; blazend-player does the local playback. Accent-insensitive + lightly
stemmed so Polish case endings collapse, reusing radio.py's folding.
"""
from __future__ import annotations

import json
import os
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

# Shared PL-fold/stem helpers now live in the audiobook-catalog domain lib
# (radio.py re-imports them privately; import from the source so mypy sees the
# explicit export).
from audiobook_catalog.text_norm import _fold, _stem_phrase

_DEFAULT_INDEX = "/var/lib/blazen/music-index.json"
_RANDOM_WORDS = frozenset({"cos", "cokolwiek", "losowo", "random", "muzyk", "muzyke", "muzyka"})
# "zagraj ALBUM ballady morderców" — the container word is filler, not part of
# the album name; strip it before matching. (Raw-text regexes: the shared
# stemmer deletes ł/diacritics outright, so token-level checks would misfire.)
_FILLER_RE = re.compile(
    r"\b(album\w*|p(ł|l)yt\w*|kr(ą|a)(ż|z)\w*|utw(ó|o)r\w*|piosenk\w*"
    r"|kawa(ł|l)\w*|nagran\w*|tracks?|songs?)\b", re.IGNORECASE)
# "zagraj CAŁEGO Kazika" / "zagraj WSZYSTKO" — an explicit everything-request.
_ALL_RE = re.compile(r"\b(ca(ł|l)(y|e|a|ego|ej|ą))\b|\bwszystk\w*|\b(all|entire|whole)\b",
                     re.IGNORECASE)
# "Zagraj wszystko" over a 2000-track library still needs a bound (the queue
# rides in one payload) — 500 tracks ≈ a full day of music before "stop".
_QUEUE_CAP = 500
# Skip dead rips: some ripped albums contain 0-byte / header-only stubs. A random
# pick that lands on one plays nothing ("no reaction"), so drop anything too small
# to hold even ~1 s of audio (128 kbps ≈ 16 kB/s) at load time.
_MIN_BYTES = 16 * 1024


def _num(entry: dict[str, object], key: str) -> int:
    """Non-negative int field of an index entry (0 = absent/garbage)."""
    try:
        return max(0, int(str(entry.get(key, 0) or 0)))
    except (TypeError, ValueError):
        return 0


def _playable(path: str) -> bool:
    try:
        return os.path.getsize(path) >= _MIN_BYTES
    except OSError:
        return False


@dataclass
class Track:
    path: str
    title: str
    artist: str
    album: str
    disc: int = 0   # album ordering (0 = unknown; from ID3 tag or "03 …" filename)
    track: int = 0
    _title: set[str] = field(default_factory=set, repr=False)
    _artist: set[str] = field(default_factory=set, repr=False)
    _album: set[str] = field(default_factory=set, repr=False)
    # Folder names ("kazik i patyczak", "EL DOPA") — many rips group a band by
    # directory but tag the ID3 artist as someone else, so match the path too.
    _folder: set[str] = field(default_factory=set, repr=False)


def _tokens(text: str) -> set[str]:
    return {t for t in _stem_phrase(text).split() if t}


def _folder_tokens(path: str) -> set[str]:
    """Tokens from the two enclosing folder names (band/collection + album)."""
    p = Path(path)
    return _tokens(f"{p.parent.name} {p.parent.parent.name}")


class MusicDirectory:
    """Loads the music index and resolves a spoken request to a track path."""

    def __init__(self, *, index_path: str | None = None) -> None:
        self.tracks: list[Track] = []
        path = Path(index_path or os.environ.get("BLAZEN_MUSIC_INDEX", _DEFAULT_INDEX))
        try:
            raw = json.loads(path.read_text(encoding="utf-8")).get("tracks", []) or []
        except (OSError, ValueError):
            raw = []
        for e in raw:
            if not isinstance(e, dict) or not e.get("path"):
                continue
            if not _playable(str(e["path"])):
                continue
            title, artist, album = str(e.get("title", "")), str(e.get("artist", "")), str(e.get("album", ""))
            self.tracks.append(Track(
                path=str(e["path"]), title=title, artist=artist, album=album,
                disc=_num(e, "disc"), track=_num(e, "track"),
                _title=_tokens(title), _artist=_tokens(artist), _album=_tokens(album),
                _folder=_folder_tokens(str(e["path"])),
            ))

    @property
    def available(self) -> bool:
        return bool(self.tracks)

    def random(self) -> Track | None:
        return random.choice(self.tracks) if self.tracks else None  # noqa: S311 (not crypto)

    def resolve(self, query: str) -> Track | None:
        """Best match for ``query`` across title/artist/album. A request that
        names an ARTIST or ALBUM (many tracks) returns a RANDOM one of them, so
        "zagraj Kazika" plays a random Kazik track; a title match is exact."""
        q = _tokens(query)
        if not q or _fold(query).strip() in _RANDOM_WORDS:
            return self.random()
        best_score = 0
        matches: list[Track] = []
        for t in self.tracks:
            score = 0
            if (q <= t._title and t._title) or (q <= t._artist and t._artist):
                # Whole query is a title OR an artist. Equal rank so a bare artist
                # name ("Kazik") pools ALL their tracks → a random one plays, while
                # a full title still matches its (usually single) track.
                score = 100
            elif (q <= t._folder and t._folder) or (q <= t._album and t._album):
                score = 90                     # whole query is a band/collection folder or album
            else:
                score = len(q & (t._title | t._artist | t._album | t._folder))  # partial overlap
            if score > best_score:
                best_score, matches = score, [t]
            elif score == best_score and score > 0:
                matches.append(t)
        if best_score == 0:
            return None
        return random.choice(matches)  # noqa: S311 — among equally-good, pick one (artist → random track)

    def _shuffled_queue(self, pool: list[Track]) -> list[Track]:
        """Dedupe by title (duplicate rips must not play a song twice),
        shuffle, cap."""
        seen: set[str] = set()
        unique: list[Track] = []
        for t in pool:
            key = " ".join(sorted(t._title)) or t.path
            if key not in seen:
                seen.add(key)
                unique.append(t)
        random.shuffle(unique)
        return unique[:_QUEUE_CAP]

    def resolve_all(self, query: str) -> list[Track] | None:
        """"Zagraj całego Kazika" / "zagraj wszystko" / "zagraj coś" — an
        everything-request: the named artist's tracks (or, with no name left,
        the whole library) as a shuffled queue. None unless the request says
        cały/wszystko or is a bare something-request."""
        if not self.tracks:
            return None
        if not (_ALL_RE.search(query) or _fold(query).strip() in _RANDOM_WORDS):
            return None
        q = _tokens(_FILLER_RE.sub(" ", _ALL_RE.sub(" ", query))) - _RANDOM_WORDS
        if q:
            pool = [t for t in self.tracks
                    if (q <= t._artist and t._artist) or (q <= t._folder and t._folder)]
        else:
            pool = list(self.tracks)
        return self._shuffled_queue(pool) if pool else None

    def resolve_artist(self, query: str) -> list[Track] | None:
        """A query that names an ARTIST (or their band folder) → ALL their
        tracks as a shuffled queue (decision 2026-07-27: artist and album
        requests play everything until "stop", not one surprise track)."""
        q = _tokens(_FILLER_RE.sub(" ", query))
        if not q or not self.tracks:
            return None
        pool = [t for t in self.tracks
                if (q <= t._artist and t._artist) or (q <= t._folder and t._folder)]
        if len(pool) < 2:
            return None  # zero/one hit is a track request, not an artist's catalogue
        return self._shuffled_queue(pool)

    def resolve_album(self, query: str) -> list[Track] | None:
        """The ordered tracks of the ALBUM the query names, or None when the
        query isn't an album-level request. A bare artist name stays a
        random-track request (resolve()), so "zagraj Kazika" keeps surprising —
        only a query that names no artist but does name an album/collection
        folder becomes a queue. The library holds several rips of the same
        album, so matches are grouped by their on-disk folder and the most
        complete single rip wins; the index has no track numbers but the rip
        filenames carry them ("01 …", "09 …") → filename order is album order."""
        q = _tokens(_FILLER_RE.sub(" ", query))
        if not q or _fold(query).strip() in _RANDOM_WORDS:
            return None
        if any(q <= t._artist and t._artist for t in self.tracks):
            return None  # artist request → single random track via resolve()
        rips: dict[str, list[Track]] = {}
        for t in self.tracks:
            if (q <= t._album and t._album) or (q <= t._folder and t._folder):
                rips.setdefault(str(Path(t.path).parent), []).append(t)
        if not rips:
            return None
        # Most complete rip wins; on a tie (the same album ripped twice), the
        # better-tagged one — more tracks carrying disc/track numbers — plays.
        best = max(rips.values(), key=lambda ts: (len(ts), sum(1 for t in ts if t.track)))
        if len(best) < 2:
            return None  # one file is a track, not an album — let resolve() handle it
        # Album order: disc+track numbers (ID3 tag / numbered filename, via the
        # index) lead; unnumbered leftovers follow alphabetically.
        return sorted(best, key=lambda t: (t.disc, t.track or 9999, Path(t.path).name.lower()))


__all__ = ["MusicDirectory", "Track"]
