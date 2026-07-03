"""On-device semantic search over the local music + audiobook library.

Loads the vector matrix + metadata built by scripts/build-semantic-index.py and,
given a spoken query, returns the nearest items by MEANING (cosine over the e5
embeddings), so "znajdź coś spokojnego" / "muzyka o miłości" / "książka o morzu"
resolve even without a literal title/artist match. The e5 embedder is loaded
lazily (only when a semantic query is actually made). Notes/emails can be added
to the same index later without touching this search.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_DEFAULT = "/var/lib/blazen/semantic-index"


class SemanticLibrary:
    def __init__(self, *, index_path: str | None = None, embedder: Any | None = None) -> None:
        base = index_path or os.environ.get("BLAZEN_SEMANTIC_INDEX", _DEFAULT)
        self._items: list[dict] = []
        self._mat = None
        self._embedder = embedder
        self._loaded_embedder = embedder is not None
        try:
            import numpy as np  # noqa: PLC0415 — optional at import time

            self._np = np
            self._mat = np.load(base + ".npy")
            self._items = json.loads(Path(base + ".json").read_text(encoding="utf-8")).get("items", [])
        except Exception:  # noqa: BLE001 — no index / no numpy → unavailable
            self._np = None

    @property
    def available(self) -> bool:
        return self._mat is not None and len(self._items) == (self._mat.shape[0] if self._mat is not None else -1)

    def _embed_query(self, query: str):
        if not self._loaded_embedder:
            try:
                from blazend.domains.context.adapters.rpi5.embeddings import Embedder  # noqa: PLC0415

                self._embedder = Embedder()
            except Exception:  # noqa: BLE001
                self._embedder = None
            self._loaded_embedder = True
        if self._embedder is None or not getattr(self._embedder, "available", False):
            return None
        vecs = self._embedder.embed([query], kind="query")
        return self._np.asarray(vecs[0], dtype=self._np.float32) if vecs else None

    def search(self, query: str, *, k: int = 3, kinds: tuple[str, ...] | None = None) -> list[dict]:
        """Top-k items most similar to ``query`` (each with a 'score'). Optionally
        restrict to item kinds ('music' / 'book')."""
        if not self.available or not query.strip():
            return []
        q = self._embed_query(query)
        if q is None:
            return []
        sims = self._mat @ q  # cosine — both are L2-normalised
        order = self._np.argsort(-sims)
        out: list[dict] = []
        for i in order:
            item = self._items[int(i)]
            if kinds and item.get("type") not in kinds:
                continue
            out.append({**item, "score": float(sims[int(i)])})
            if len(out) >= k:
                break
        return out


__all__ = ["SemanticLibrary"]
