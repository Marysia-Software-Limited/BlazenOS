"""Offline audiobook directory — resolve a spoken request to a Polish audiobook.

Backs "włącz książkę <tytuł/autor>" / "znajdź <tytuł>". The catalogue is a
catalog.json (built by scripts/fetch-wolnelektury.py) of books with ordered MP3
chapter paths on the device, loaded from ``$BLAZEN_AUDIOBOOKS_CATALOG`` or
``/var/lib/blazen/audiobooks/catalog.json``. Pure resolve; blazend-player plays
the chapters locally. Accent-folded/stemmed like radio.py so Polish endings and
diacritics don't matter. `offer()` feeds the spoken menu for a blind user.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.radio import _fold, _stem_phrase

_DEFAULT = "/var/lib/blazen/audiobooks/catalog.json"


def _tokens(text: str) -> set[str]:
    return {t for t in _stem_phrase(text).split() if t}


@dataclass
class Book:
    title: str
    author: str
    chapters: tuple[str, ...]
    slug: str = ""                       # stable id — the progress key
    _title: set[str] = field(default_factory=set, repr=False)
    _author: set[str] = field(default_factory=set, repr=False)


class AudiobookDirectory:
    def __init__(self, *, catalog_path: str | None = None) -> None:
        self.books: list[Book] = []
        path = Path(catalog_path or os.environ.get("BLAZEN_AUDIOBOOKS_CATALOG", _DEFAULT))
        try:
            raw = json.loads(path.read_text(encoding="utf-8")).get("books", []) or []
        except (OSError, ValueError):
            raw = []
        for b in raw:
            chapters = tuple(str(c) for c in b.get("chapters", []) if c)
            if not chapters:
                continue
            title, author = str(b.get("title", "")), str(b.get("author", ""))
            slug = str(b.get("slug", "")) or _fold(title).replace(" ", "-")
            self.books.append(Book(title=title, author=author, chapters=chapters, slug=slug,
                                   _title=_tokens(title), _author=_tokens(author)))

    def by_slug(self, slug: str) -> Book | None:
        return next((b for b in self.books if b.slug == slug), None)

    @property
    def available(self) -> bool:
        return bool(self.books)

    # Stemmed volume markers → order, so a bare "Ziemia obiecana" starts at tom
    # pierwszy rather than a later volume. "i"/roman-I is omitted (it collides with
    # the "i" conjunction); Polish word forms cover the common case.
    _VOL = {"pierwsz": 1, "drug": 2, "trzec": 3, "czwart": 4, "ii": 2, "iii": 3, "iv": 4}

    def resolve(self, query: str) -> Book | None:
        """Best book match for ``query`` across title + author (longest overlap).
        On an otherwise-equal match, prefer the earliest volume so a bare
        "Ziemia obiecana" resolves to tom pierwszy, not a later volume. When the
        query itself names a volume, that scores higher and wins outright."""
        q = _tokens(query)
        if not q:
            return None
        best_key = (0, 1)                            # (score, -volrank); score 0 = no match
        best: Book | None = None
        for b in self.books:
            if q <= b._title and b._title:
                score = 100 + len(q & b._title)     # full title match, prefer more tokens
            elif q <= b._author and b._author:
                score = 60
            else:
                score = len(q & (b._title | b._author))
            vol = next((self._VOL[t] for t in b._title if t in self._VOL), 0)
            key = (score, -vol)                      # tie → smaller volume (0 = none) wins
            if key > best_key:
                best_key, best = key, b
        return best if best_key[0] > 0 else None

    def offer(self, limit: int = 6) -> list[Book]:
        """The books to read back for the spoken menu."""
        return self.books[:limit]


__all__ = ["AudiobookDirectory", "Book"]
