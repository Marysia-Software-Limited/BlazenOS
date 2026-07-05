"""Multi-layer RAG for book (and music) recommendations.

Spoken requests mix MEANING ("coś spokojnego do słuchania") with STRUCTURED
signals — a genre ("przygodowego"), an epoch ("klasyka", "romantyczne") or a
nationality ("francuska klasyka"). This engine layers them:

1. **Ontology** (``configs/ontology/books.json``) parses the query into genre /
   epoch / nationality signals and expands it with retrieval-friendly terms.
2. **Semantic** (:class:`SemanticLibrary`) retrieves candidates by meaning over
   the expanded query (e5 embeddings, on-device).
3. **Metadata + ontology rerank** boosts candidates whose genre/epoch/author-
   nationality match the signals — a nationality request hard-prefers matching
   authors (so "francuska klasyka" surfaces Dumas/Balzac, not a stray poem).

The result is a short, ranked candidate list. The *reasoning* layer (the brain's
DSPy-compiled prompt run through the ModelRouter) picks one and phrases the pitch;
when no LLM is reachable the engine still returns a solid top pick for a template
reply. Everything here is on-device and deterministic.
"""
from __future__ import annotations

import json
import os
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_PL = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")
_DEFAULT_ONTOLOGY = "/etc/blazen/ontology/books.json"


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKC", str(text)).translate(_PL).lower().strip()


@dataclass
class Signals:
    """Structured signals parsed from a spoken query."""

    genres: list[str] = field(default_factory=list)         # folded catalogue-genre substrings
    epochs: list[str] = field(default_factory=list)         # folded catalogue-epoch substrings
    nationalities: list[str] = field(default_factory=list)  # nationality tags (folded)
    expansion: str = ""                                     # extra text for the embedder

    @property
    def any(self) -> bool:
        return bool(self.genres or self.epochs or self.nationalities)


class Ontology:
    """Loads ``books.json`` and turns a query into :class:`Signals`."""

    def __init__(self, *, path: str | None = None) -> None:
        base = path or os.environ.get("BLAZEN_ONTOLOGY", _DEFAULT_ONTOLOGY)
        try:
            data = json.loads(Path(base).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        self._genre_syn: dict[str, list[str]] = data.get("genre_synonyms", {}) or {}
        self._epoch_syn: dict[str, list[str]] = data.get("epoch_synonyms", {}) or {}
        self._nat_syn: dict[str, str] = data.get("nationality_synonyms", {}) or {}
        # author (verbatim) → nationality tag; matched case/accent-insensitively.
        self._author_nat: dict[str, str] = {
            _fold(k): v for k, v in (data.get("author_nationality", {}) or {}).items()
        }

    @property
    def available(self) -> bool:
        return bool(self._genre_syn or self._epoch_syn or self._nat_syn)

    def nationality_of(self, author: str) -> str | None:
        return self._author_nat.get(_fold(author))

    def parse(self, query: str) -> Signals:
        q = _fold(query)
        tokens = q.split()

        def hit(term: str) -> bool:
            # Multiword phrases ("dla dzieci") → substring; single terms → token
            # PREFIX match so Polish inflection is caught ("przygodowe" ≺
            # "przygodowego") WITHOUT embedded-word false positives ("antyczne"
            # inside "romantyczne").
            if " " in term:
                return term in q
            return any(
                tok.startswith(term) or (len(tok) >= 5 and term.startswith(tok))
                for tok in tokens
            )

        genres: list[str] = []
        epochs: list[str] = []
        nats: list[str] = []
        extra: list[str] = []
        for term, subs in self._genre_syn.items():
            if hit(term):
                genres.extend(subs)
        for term, epvals in self._epoch_syn.items():
            if hit(term):
                epochs.extend(epvals)
                extra.append(term)
        for term, tag in self._nat_syn.items():
            if hit(term):
                nats.append(tag)
                extra.append(f"{tag} autor")
        return Signals(
            genres=sorted(set(genres)),
            epochs=sorted(set(epochs)),
            nationalities=sorted(set(nats)),
            expansion=" ".join(dict.fromkeys(extra)),
        )


class RecommendationEngine:
    """Semantic + metadata + ontology → a ranked candidate list."""

    # Rerank boosts added to the cosine score (0..1); nationality is the most
    # specific ask, so it dominates.
    _GENRE_BOOST = 0.15
    _EPOCH_BOOST = 0.12
    _NAT_BOOST = 0.25

    def __init__(self, *, semantic: Any, ontology: Ontology | None = None) -> None:
        self.semantic = semantic
        self.ontology = ontology or Ontology()

    def _boost(self, item: dict[str, Any], sig: Signals) -> float:
        b = 0.0
        genre = _fold(item.get("genre", ""))
        epoch = _fold(item.get("epoch", ""))
        if sig.genres and any(g in genre for g in sig.genres):
            b += self._GENRE_BOOST
        if sig.epochs and any(e in epoch for e in sig.epochs):
            b += self._EPOCH_BOOST
        if sig.nationalities:
            nat = self.ontology.nationality_of(item.get("who", ""))
            if nat and nat in sig.nationalities:
                b += self._NAT_BOOST
        return b

    def candidates(self, query: str, *, kinds: tuple[str, ...] = ("book",), k: int = 6) -> list[dict[str, Any]]:
        """Top-``k`` candidates for ``query``, reranked by ontology/metadata match."""
        sig = self.ontology.parse(query)
        expanded = f"{query} {sig.expansion}".strip()
        # Retrieve a wide net, then rerank; a nationality/genre ask needs headroom
        # to pull matching authors up from below the pure-semantic top.
        hits = self.semantic.search(expanded, k=max(k * 4, 24), kinds=kinds)
        scored = [(float(h.get("score", 0.0)) + self._boost(h, sig), h) for h in hits]
        # A nationality request hard-prefers matching authors when any exist.
        if sig.nationalities:
            matches = [(s, h) for s, h in scored
                       if self.ontology.nationality_of(h.get("who", "")) in sig.nationalities]
            if matches:
                rest = [(s, h) for s, h in scored if (s, h) not in matches]
                scored = sorted(matches, key=lambda x: -x[0]) + sorted(rest, key=lambda x: -x[0])
                return [h for _, h in scored[:k]]
        scored.sort(key=lambda x: -x[0])
        return [h for _, h in scored[:k]]


__all__ = ["Ontology", "RecommendationEngine", "Signals"]
