"""Mac-local blazen paths for the rachel audiobook library.

rachel shares one audiobook library with the rest of the constellation: the
mesh-merged catalog + progress live under ``~/audiobooks`` (same location the
Pi/paul use via ``BLAZEN_AUDIOBOOKS_DIR`` and the launchd pull timer), so
``rachel-audiobook`` resolves BOTH rachel's own renders and books rendered on
paul (whose chapters are streamed, not re-rendered). Each book is
``<root>/<slug>/NN.mp3`` with ``catalog.json`` + ``progress.json`` alongside —
the shared schema. Overrides, in precedence order:

- ``BLAZEN_AUDIOBOOKS_ROOT`` — the library root (used by tests).
- ``BLAZEN_AUDIOBOOKS_DIR`` — the constellation's library var (jessica/paul).
- ``BLAZEN_AUDIOBOOKS_CATALOG`` / ``BLAZEN_AUDIOBOOK_PROGRESS`` — pin those files
  directly (what the launchd agents set).
"""
from __future__ import annotations

import os
from pathlib import Path


def audiobook_root() -> Path:
    env = os.environ.get("BLAZEN_AUDIOBOOKS_ROOT") or os.environ.get("BLAZEN_AUDIOBOOKS_DIR")
    base = Path(env) if env else Path.home() / "audiobooks"
    base.mkdir(parents=True, exist_ok=True)
    return base


def catalog_path() -> Path:
    env = os.environ.get("BLAZEN_AUDIOBOOKS_CATALOG")
    return Path(env) if env else audiobook_root() / "catalog.json"


def progress_path() -> Path:
    env = os.environ.get("BLAZEN_AUDIOBOOK_PROGRESS")
    return Path(env) if env else audiobook_root() / "progress.json"


__all__ = ["audiobook_root", "catalog_path", "progress_path"]
