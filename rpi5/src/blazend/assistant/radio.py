"""Internet-radio directory — resolve a spoken station name to a stream.

Backs the "włącz Trójkę" / "play Radio Kraków" intent. The catalogue lives in
``configs/radio.yaml`` (verified stream URLs). This module is pure: it loads the
station list and matches a spoken phrase to a station (accent-insensitive, so
"trojka" matches "Trójka"). Actually *playing* the stream is the runner's job
(:class:`blazend.voice.runner.StreamPlayer`) — the engine only routes.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from blazend.config import load

_VOWELS = frozenset("aeiouy")


def _fold(text: str) -> str:
    """Lower-case and strip diacritics ('Trójkę' → 'trojke')."""
    nfkd = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _stem_token(tok: str) -> str:
    """Crudely drop one trailing vowel so Polish case endings collapse
    ('trojke'/'trojka'/'trojki' → 'trojk'). Only for tokens long enough that
    the stem stays meaningful."""
    return tok[:-1] if len(tok) > 3 and tok[-1] in _VOWELS else tok


def _stem_phrase(text: str) -> str:
    """Fold, tokenise and stem; return a space-delimited token string padded
    with spaces so callers can test whole-token containment."""
    toks = re.findall(r"[a-z0-9]+", _fold(text))
    return " " + " ".join(_stem_token(t) for t in toks) + " "


@dataclass
class Station:
    id: str
    name: str
    url: str
    aliases: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    default: bool = False
    _folded: tuple[str, ...] = field(default=(), repr=False)


class RadioDirectory:
    """Loads the station catalogue and resolves spoken names to streams."""

    def __init__(self, *, config_name: str = "radio") -> None:
        self.stations: list[Station] = []
        try:
            raw = load(config_name).get("stations", []) or []
        except Exception:  # noqa: BLE001 — no config root (tests) → empty directory
            raw = []
        for entry in raw:
            if not isinstance(entry, dict) or not entry.get("url"):
                continue
            aliases = tuple(str(a) for a in entry.get("aliases", []))
            name = str(entry.get("name", entry.get("id", "")))
            # Stem the name + every alias for matching; longest first so a
            # specific alias ("off radio kraków") beats a generic one ("kraków").
            folded = tuple(sorted(
                {_stem_phrase(name).strip(), *(_stem_phrase(a).strip() for a in aliases)} - {""},
                key=len, reverse=True,
            ))
            self.stations.append(Station(
                id=str(entry.get("id", name)), name=name, url=str(entry["url"]),
                aliases=aliases, tags=tuple(str(t) for t in entry.get("tags", [])),
                default=bool(entry.get("default", False)), _folded=folded,
            ))

    @property
    def available(self) -> bool:
        return bool(self.stations)

    def default_station(self) -> Station | None:
        for s in self.stations:
            if s.default:
                return s
        return self.stations[0] if self.stations else None

    def resolve(self, text: str) -> Station | None:
        """Find the station named in ``text`` (longest alias match wins)."""
        haystack = _stem_phrase(text)  # space-padded, stemmed tokens
        best: tuple[int, Station] | None = None
        for station in self.stations:
            for alias in station._folded:  # noqa: SLF001 (own dataclass)
                if alias and f" {alias} " in haystack and (best is None or len(alias) > best[0]):
                    best = (len(alias), station)
        return best[1] if best else None

    def offer(self, limit: int = 4) -> list[Station]:
        """The stations to read back when the user just says 'radio'."""
        return self.stations[:limit]


__all__ = ["RadioDirectory", "Station"]
