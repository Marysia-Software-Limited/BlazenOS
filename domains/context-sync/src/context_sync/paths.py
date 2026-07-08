"""Where a node persists its shared-context stores (``memory.json`` +
``progress.json``).

Blazend-free path resolution so the fabric path (`jessica --serve-fabric` /
`--sync`) works on nodes that don't ship the Pi appliance package (rachel, macOS).
Mirrors the appliance's ``MemoryStore`` data dir **exactly** (env-driven), so
paul / the Pi resolve the same file they always have; other nodes just point
``BLAZEN_DATA_DIR`` at their own store.
"""
from __future__ import annotations

import os
from pathlib import Path


def data_dir() -> Path:
    """This node's context data directory. Override with ``BLAZEN_DATA_DIR``."""
    root = os.environ.get("BLAZEN_DATA_DIR")
    if root:
        return Path(root)
    runtime = os.environ.get("BLAZEN_RUNTIME_DIR", f"/tmp/blazen-{os.getuid()}")
    return Path(runtime) / "data"


def context_paths(explicit: str | os.PathLike[str] | None = None) -> tuple[Path, Path]:
    """``(memory.json, progress.json)`` for this node's shared context.

    ``explicit`` (the CLI's ``--data`` flag) overrides the memory path; its parent
    directory hosts the sibling ``progress.json``.
    """
    mem = Path(explicit) if explicit else data_dir() / "memory.json"
    return mem, mem.parent / "progress.json"
