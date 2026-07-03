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
            self.books.append(Book(title=title, author=author, chapters=chapters,
                                   _title=_tokens(title), _author=_tokens(author)))

    @property
    def available(self) -> bool:
        return bool(self.books)

    def resolve(self, query: str) -> Book | None:
        """Best book match for ``query`` across title + author (longest overlap)."""
        q = _tokens(query)
        if not q:
            return None
        best_score = 0
        best: Book | None = None
        for b in self.books:
            if q <= b._title and b._title:
                score = 100 + len(q & b._title)     # full title match, prefer more tokens
            elif q <= b._author and b._author:
                score = 60
            else:
                score = len(q & (b._title | b._author))
            if score > best_score:
                best_score, best = score, b
        return best if best_score > 0 else None

    def offer(self, limit: int = 6) -> list[Book]:
        """The books to read back for the spoken menu."""
        return self.books[:limit]


__all__ = ["AudiobookDirectory", "Book"]
