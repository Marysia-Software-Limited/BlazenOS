"""Audiobook resume point survives the start gap and a mid-chapter player death.

2026-10-12 live bug on paul: "czytaj Metro" after a stop restarted from the
top. The book watcher ticks every second and reads "book set + player not
running" as a chapter EOF; when its tick landed in the audio-out release gap
between arming the book and spawning the player it dropped ``_book``, so the
later stop had nothing to save. Here the gap is stretched past one tick and
the book must still be tracked (and its position saved on stop).
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from audiobook_catalog.progress import AudiobookProgress

from blazend.domains.systems.adapters.rpi5.orchestrator.supervisor import Orchestrator

REPO = Path(__file__).resolve().parents[2]


class _Radio:
    """Exclusive player stand-in: alive after play(), writes the position file."""

    def __init__(self) -> None:
        self.playing = False
        self.position_file = ""

    def play(self, url: str, name: str = "", *, position_file: str = "",
             start_seconds: float = 0.0, speech: bool = False) -> None:
        self.playing = True
        self.position_file = position_file
        Path(position_file).write_text(json.dumps({"seconds": start_seconds, "done": False}))

    def stop(self) -> None:
        self.playing = False


@pytest.fixture
def orch(tmp_path, monkeypatch):
    monkeypatch.setenv("BLAZEN_CONFIG_ROOT", str(REPO / "configs"))
    monkeypatch.setenv("BLAZEN_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("BLAZEN_AUDIOBOOK_PROGRESS", raising=False)
    o = Orchestrator(peers=("wake",), runtime_dir_=tmp_path)
    o._radio = _Radio()  # type: ignore[assignment]

    async def slow_release(up: bool) -> None:  # the audio-out hand-over gap
        await asyncio.sleep(1.3)  # > one watcher tick

    async def noop() -> None:
        return None

    o._apply_audio_out = slow_release  # type: ignore[method-assign]
    o._duck_off = noop  # type: ignore[method-assign]
    return o


_BOOK = {"action": "music_play", "payload": {
    "path": "/books/metro/01.mp3", "name": "Metro 2033", "is_audiobook": True,
    "slug": "calibre-35", "chapters": ["/books/metro/01.mp3", "/books/metro/02.mp3"],
    "chapter": 0, "start_seconds": 0.0}}


@pytest.mark.asyncio
async def test_book_survives_start_gap_and_stop_saves_position(orch, tmp_path):
    watcher = asyncio.create_task(orch._book_watcher())
    try:
        await orch._act_on_reply(_BOOK)
        assert orch._book is not None and orch._book["slug"] == "calibre-35"
        assert orch._book_stopping is False  # EOF tracking re-armed once the player is up
        await asyncio.sleep(1.2)  # a tick while it plays must not drop it either
        assert orch._book is not None
        (tmp_path / "player-position").write_text(json.dumps({"seconds": 30.0, "done": False}))
        await orch._act_on_reply({"action": "music_stop"})
    finally:
        orch._stop.set()
        await watcher
    saved = AudiobookProgress().get("calibre-35")
    assert saved is not None and saved["chapter"] == 0 and saved["offset_s"] == 30.0


@pytest.mark.asyncio
async def test_player_death_mid_chapter_keeps_the_place(orch, tmp_path):
    watcher = asyncio.create_task(orch._book_watcher())
    try:
        await orch._act_on_reply(_BOOK)
        (tmp_path / "player-position").write_text(json.dumps({"seconds": 12.0, "done": False}))
        orch._radio.playing = False  # died (ALSA busy, SIGKILL) — not a user stop
        await asyncio.sleep(1.3)
        assert orch._book is None  # watcher noticed the exit
    finally:
        orch._stop.set()
        await watcher
    saved = AudiobookProgress().get("calibre-35")
    assert saved is not None and saved["offset_s"] == 12.0
