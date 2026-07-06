"""Mac-local blazen paths for the rachel audiobook library.

Rendered Calibre audiobooks live under
``~/Library/Application Support/blazen/audiobooks/<slug>/NN.mp3`` with a
``catalog.json`` + ``progress.json`` alongside — the same schema the Pi uses,
so a later sync to the shared Pi catalog is a copy, not a conversion. Override
the root with ``BLAZEN_AUDIOBOOKS_ROOT`` (used by tests).
"""
from __future__ import annotations

import os
from pathlib import Path


def audiobook_root() -> Path:
    env = os.environ.get("BLAZEN_AUDIOBOOKS_ROOT")
    base = Path(env) if env else Path.home() / "Library/Application Support/blazen/audiobooks"
    base.mkdir(parents=True, exist_ok=True)
    return base


def catalog_path() -> Path:
    return audiobook_root() / "catalog.json"


def progress_path() -> Path:
    return audiobook_root() / "progress.json"


__all__ = ["audiobook_root", "catalog_path", "progress_path"]
