"""Atomic /run/blazen/state.json writer.

Single source of truth for "what state is the system in right now" —
read by SSH admin scripts, the e2e runner, and Tier 1 tests.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from blazend.ipc import runtime_dir


class StateWriter:
    """Atomically writes ``state.json`` to the runtime directory."""

    def __init__(self, path: Path | None = None):
        self._path = path or (runtime_dir() / "state.json")
        self._lock = asyncio.Lock()
        self._state: dict[str, Any] = {"v": 1, "ready": False}

    @property
    def path(self) -> Path:
        """Where the file is written."""
        return self._path

    async def update(self, patch: dict[str, Any]) -> None:
        """Merge *patch* into the in-memory state and flush to disk."""
        async with self._lock:
            _deep_merge(self._state, patch)
            await self._flush_locked()

    async def replace(self, state: dict[str, Any]) -> None:
        """Replace the in-memory state entirely and flush."""
        async with self._lock:
            self._state = state
            await self._flush_locked()

    async def snapshot(self) -> dict[str, Any]:
        """Return a deep copy of the current state."""
        async with self._lock:
            return json.loads(json.dumps(self._state))

    async def _flush_locked(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".part")
        await asyncio.to_thread(
            _write_atomic,
            tmp,
            self._path,
            json.dumps(self._state, indent=2, ensure_ascii=False).encode("utf-8"),
        )


def _write_atomic(tmp: Path, dst: Path, payload: bytes) -> None:
    with tmp.open("wb") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, dst)


def _deep_merge(dst: dict[str, Any], src: dict[str, Any]) -> None:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = v


__all__ = ["StateWriter"]
