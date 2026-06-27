"""Tier 1 — atomic state.json writer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from blazend.domains.systems.adapters.rpi5.state import StateWriter


@pytest.mark.asyncio
async def test_initial_state(tmp_path: Path):
    writer = StateWriter(tmp_path / "state.json")
    snap = await writer.snapshot()
    assert snap["ready"] is False
    assert snap["v"] == 1


@pytest.mark.asyncio
async def test_patch_writes_atomically(tmp_path: Path):
    path = tmp_path / "state.json"
    writer = StateWriter(path)
    await writer.update({"ready": True, "led": "green"})
    assert path.is_file()
    data = json.loads(path.read_text())
    assert data["ready"] is True
    assert data["led"] == "green"
    # No leftover .part file.
    assert not list(tmp_path.glob("*.part"))


@pytest.mark.asyncio
async def test_deep_merge(tmp_path: Path):
    writer = StateWriter(tmp_path / "state.json")
    await writer.update({"units": {"a": {"status": "ok"}}})
    await writer.update({"units": {"b": {"status": "ok"}}})
    snap = await writer.snapshot()
    assert snap["units"] == {"a": {"status": "ok"}, "b": {"status": "ok"}}


@pytest.mark.asyncio
async def test_replace_clears_old_keys(tmp_path: Path):
    writer = StateWriter(tmp_path / "state.json")
    await writer.update({"foo": 1, "bar": 2})
    await writer.replace({"baz": 3})
    snap = await writer.snapshot()
    assert snap == {"baz": 3}
