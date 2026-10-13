"""Per-book listening progress — remember + restore where a book was stopped.

Moved from rpi5 ``assistant/audiobook_progress.py`` into the shared domains lib.
Audiobooks are long; a listener stops mid-chapter and expects to pick up exactly
where they left off. Stores, per book ``slug``, the current chapter index +
offset in seconds, persisted to ``$BLAZEN_AUDIOBOOK_PROGRESS`` or a given path
(atomic write). Keyed by the stable Wolne-Lektury / calibre slug so it survives
catalogue rebuilds.

Default location (2026-10-12): ``$BLAZEN_DATA_DIR/progress.json`` — the same
file the fabric's context sync (``context_sync.context_paths``) already carries
between nodes, so a book stopped on one node resumes on another. Every install
(appliance ``/var/lib/blazen/data``, desktop ``~/.local/share/blazen/data``)
sets ``BLAZEN_DATA_DIR`` and can write there; the old appliance-only
``/var/lib/blazen/audiobooks/progress.json`` was unwritable on desktop installs,
so paul silently never remembered a position and every book restarted from the
top. It is still read once as a fallback (migration) when the new file is absent.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

_LEGACY = "/var/lib/blazen/audiobooks/progress.json"
_log = logging.getLogger("audiobook_catalog.progress")


def default_progress_path() -> Path:
    """Where this node keeps its listening progress (env-driven, see module doc)."""
    explicit = os.environ.get("BLAZEN_AUDIOBOOK_PROGRESS")
    if explicit:
        return Path(explicit)
    data = os.environ.get("BLAZEN_DATA_DIR")
    if data:
        return Path(data) / "progress.json"
    return Path(_LEGACY)


class AudiobookProgress:
    def __init__(self, *, path: str | None = None) -> None:
        self._path = Path(path) if path else default_progress_path()
        self._d: dict[str, dict[str, Any]] = self._load(self._path)
        if not self._d and path is None and self._path != Path(_LEGACY):
            self._d = self._load(Path(_LEGACY))  # one-time migration from the old spot

    @staticmethod
    def _load(path: Path) -> dict[str, dict[str, Any]]:
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return d if isinstance(d, dict) else {}

    @property
    def path(self) -> Path:
        return self._path

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
        except OSError as exc:
            # Loud, not silent: an unwritable store means "the book always
            # restarts from the beginning" — the symptom must point here.
            _log.warning("audiobook progress not saved to %s: %s", self._path, exc)


__all__ = ["AudiobookProgress", "default_progress_path"]
