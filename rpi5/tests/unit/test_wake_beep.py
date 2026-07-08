"""Wake earcon (#49): the instant "dżesika → beep → speak" chime.

Covers the pure WAV generator, the arm gate (audio.yaml earcon + device-free), and
that a ``wake.detected`` envelope schedules the beep exactly when armed.
"""
from __future__ import annotations

import io
import types
import wave
from pathlib import Path

import pytest

from blazend.domains.systems.adapters.rpi5.orchestrator.supervisor import (
    Orchestrator,
    _make_wake_beep_wav,
)
from blazend.events import wake_detected

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _config_root(monkeypatch):
    monkeypatch.setenv("BLAZEN_CONFIG_ROOT", str(REPO / "configs"))


def test_beep_wav_is_valid_short_mono_pcm():
    data = _make_wake_beep_wav(rate=22050)
    with wave.open(io.BytesIO(data)) as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == 22050
        # two 75 ms notes ≈ 150 ms — short enough to be pre-verbal, non-empty.
        secs = w.getnframes() / w.getframerate()
        assert 0.12 < secs < 0.20


def test_beep_wav_is_deterministic():
    assert _make_wake_beep_wav() == _make_wake_beep_wav()  # identical every boot


def _orch(tmp_path: Path, *, chime: bool, radio_playing: bool) -> Orchestrator:
    orch = Orchestrator(peers=("wake",), runtime_dir_=tmp_path)
    orch._earcons = dict(orch._earcons, wake_chime=chime)
    orch._radio = types.SimpleNamespace(playing=radio_playing)  # type: ignore[assignment]
    return orch


def test_armed_only_when_enabled_and_device_free(tmp_path):
    assert _orch(tmp_path, chime=True, radio_playing=False)._wake_chime_armed()
    assert not _orch(tmp_path, chime=False, radio_playing=False)._wake_chime_armed()
    # a playing stream owns the Jabra; its duck is the feedback, so no chime.
    assert not _orch(tmp_path, chime=True, radio_playing=True)._wake_chime_armed()


@pytest.mark.asyncio
async def test_wake_detected_schedules_beep_when_armed(tmp_path):
    orch = _orch(tmp_path, chime=True, radio_playing=False)
    played: list[bool] = []

    async def _fake_beep() -> None:
        played.append(True)

    orch._play_wake_beep = _fake_beep  # type: ignore[method-assign]
    env = wake_detected(source="blazend-wake", model="dzesika_pl", score=0.9, language="pl")
    await orch._on_envelope("wake", env)
    import asyncio
    await asyncio.sleep(0)  # let the fire-and-forget task run
    assert played == [True]


@pytest.mark.asyncio
async def test_wake_detected_silent_when_disabled(tmp_path):
    orch = _orch(tmp_path, chime=False, radio_playing=False)
    played: list[bool] = []

    async def _fake_beep() -> None:
        played.append(True)

    orch._play_wake_beep = _fake_beep  # type: ignore[method-assign]
    env = wake_detected(source="blazend-wake", model="dzesika_pl", score=0.9, language="pl")
    await orch._on_envelope("wake", env)
    import asyncio
    await asyncio.sleep(0)
    assert played == []
