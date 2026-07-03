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
from dataclasses import dataclass, field
from pathlib import Path

from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.radio import _fold, _stem_phrase

_DEFAULT_INDEX = "/var/lib/blazen/music-index.json"
_RANDOM_WORDS = frozenset({"cos", "cokolwiek", "losowo", "random", "muzyk", "muzyke", "muzyka"})


@dataclass
class Track:
    path: str
    title: str
    artist: str
    album: str
    _title: set[str] = field(default_factory=set, repr=False)
    _artist: set[str] = field(default_factory=set, repr=False)
    _album: set[str] = field(default_factory=set, repr=False)


def _tokens(text: str) -> set[str]:
    return {t for t in _stem_phrase(text).split() if t}


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
            title, artist, album = str(e.get("title", "")), str(e.get("artist", "")), str(e.get("album", ""))
            self.tracks.append(Track(
                path=str(e["path"]), title=title, artist=artist, album=album,
                _title=_tokens(title), _artist=_tokens(artist), _album=_tokens(album),
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
            elif q <= t._album and t._album:
                score = 85                     # whole query is the album
            else:
                score = len(q & (t._title | t._artist | t._album))  # partial overlap
            if score > best_score:
                best_score, matches = score, [t]
            elif score == best_score and score > 0:
                matches.append(t)
        if best_score == 0:
            return None
        return random.choice(matches)  # noqa: S311 — among equally-good, pick one (artist → random track)


__all__ = ["MusicDirectory", "Track"]
