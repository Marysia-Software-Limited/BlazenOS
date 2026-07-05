"""Per-book listening progress — remember + restore where a book was stopped.

Audiobooks are long; a listener stops mid-chapter (or falls asleep during the
attention check) and expects to pick up exactly where they left off. This stores,
per book ``slug``, the current chapter index + offset in seconds, persisted to
``/var/lib/blazen/audiobooks/progress.json`` (next to catalog.json, owned by the
blazen service user). Keyed by the stable Wolne-Lektury / calibre slug so it
survives catalogue rebuilds. Mirrors the load/atomic-save shape of MemoryStore.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_DEFAULT = "/var/lib/blazen/audiobooks/progress.json"


class AudiobookProgress:
    def __init__(self, *, path: str | None = None) -> None:
        self._path = Path(path or os.environ.get("BLAZEN_AUDIOBOOK_PROGRESS", _DEFAULT))
        try:
            self._d: dict[str, dict[str, Any]] = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._d = {}

    def get(self, slug: str) -> dict[str, Any] | None:
        """Saved progress for ``slug`` — ``{chapter, offset_s, title, updated}`` — or None."""
        return self._d.get(slug) if slug else None

    def save(self, slug: str, *, chapter: int, offset_s: float, title: str = "",
             updated: str = "") -> None:
        """Record the resume point for ``slug`` and persist atomically. Pass a
        real timestamp in ``updated`` (this module has no clock)."""
        if not slug:
            return
        self._d[slug] = {
            "chapter": int(max(0, chapter)),
            "offset_s": float(max(0.0, offset_s)),
            "title": title,
            "updated": updated,
        }
        self._flush()

    def clear(self, slug: str) -> None:
        """Forget a book (e.g. finished to the end)."""
        if slug in self._d:
            del self._d[slug]
            self._flush()

    def _flush(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self._d, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self._path)
        except OSError:
            pass


__all__ = ["AudiobookProgress"]
