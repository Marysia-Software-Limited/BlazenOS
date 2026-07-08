"""Tests for the blazend-free context-store path resolver.

Locks the env-driven behaviour so paul / the Pi resolve the same files the
appliance's ``MemoryStore`` always did, while rachel (macOS) can point
``BLAZEN_DATA_DIR`` at its own store.
"""
from __future__ import annotations

import os
from pathlib import Path

from context_sync import context_paths, data_dir


def test_data_dir_honours_blazen_data_dir(monkeypatch):
    monkeypatch.setenv("BLAZEN_DATA_DIR", "/srv/blazen")
    assert data_dir() == Path("/srv/blazen")


def test_data_dir_falls_back_to_runtime(monkeypatch):
    monkeypatch.delenv("BLAZEN_DATA_DIR", raising=False)
    monkeypatch.setenv("BLAZEN_RUNTIME_DIR", "/run/blazen")
    assert data_dir() == Path("/run/blazen/data")


def test_data_dir_default_matches_appliance(monkeypatch):
    monkeypatch.delenv("BLAZEN_DATA_DIR", raising=False)
    monkeypatch.delenv("BLAZEN_RUNTIME_DIR", raising=False)
    assert data_dir() == Path(f"/tmp/blazen-{os.getuid()}") / "data"


def test_context_paths_default_pair(monkeypatch):
    monkeypatch.setenv("BLAZEN_DATA_DIR", "/srv/blazen")
    mem, prog = context_paths(None)
    assert mem == Path("/srv/blazen/memory.json")
    assert prog == Path("/srv/blazen/progress.json")


def test_context_paths_explicit_override_keeps_sibling_progress():
    mem, prog = context_paths("/home/x/audiobooks/memory.json")
    assert mem == Path("/home/x/audiobooks/memory.json")
    assert prog == Path("/home/x/audiobooks/progress.json")
