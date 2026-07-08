"""Spoken state cues (blind-first UX): the orchestrator turns ASR faults into
speech instead of silence.

- ``asr.no_text``   → "Nie zrozumiałam." (there was audio, no words)   [#50]
- ``asr.no_speech`` → "Słucham?"        (wake fired, empty window)     [#49 follow-up]

The "Słucham?" prompt is rate-limited so the over-firing wake model can't chant it.
"""
from __future__ import annotations

import types
from pathlib import Path

import pytest

from blazend.domains.systems.adapters.rpi5.orchestrator.supervisor import (
    _LISTENING_CUE_COOLDOWN_S,
    Orchestrator,
)
from blazend.events import Envelope

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _config_root(monkeypatch):
    monkeypatch.setenv("BLAZEN_CONFIG_ROOT", str(REPO / "configs"))


def _orch(tmp_path: Path, *, radio_playing: bool = False) -> tuple[Orchestrator, list[str]]:
    orch = Orchestrator(peers=("asr",), runtime_dir_=tmp_path)
    orch._require_wake = False  # _awake() → always True in the test
    orch._radio = types.SimpleNamespace(playing=radio_playing)  # type: ignore[assignment]
    orch._earcons = dict(orch._earcons, error_tone=True)
    spoken: list[str] = []

    async def _fake_speak(text: str, lang: str = "pl") -> None:
        spoken.append(text)

    orch._speak = _fake_speak  # type: ignore[method-assign]
    return orch, spoken


def _err(code: str) -> Envelope:
    return Envelope(topic="error", source="blazend-asr", data={"code": code, "message": code})


@pytest.mark.asyncio
async def test_no_speech_prompts_slucham(tmp_path):
    orch, spoken = _orch(tmp_path)
    await orch._on_envelope("asr", _err("asr.no_speech"))
    assert spoken == [orch._cues["listening"]]
    assert orch._cues["listening"] == "Słucham?"


@pytest.mark.asyncio
async def test_no_speech_is_rate_limited(tmp_path):
    orch, spoken = _orch(tmp_path)
    await orch._on_envelope("asr", _err("asr.no_speech"))
    await orch._on_envelope("asr", _err("asr.no_speech"))  # within cooldown → suppressed
    assert spoken == [orch._cues["listening"]]


@pytest.mark.asyncio
async def test_no_speech_prompts_again_after_cooldown(tmp_path):
    orch, spoken = _orch(tmp_path)
    await orch._on_envelope("asr", _err("asr.no_speech"))
    # Simulate the cooldown elapsing by backdating the last-cue timestamp.
    orch._listening_cue_at -= _LISTENING_CUE_COOLDOWN_S + 1
    await orch._on_envelope("asr", _err("asr.no_speech"))
    assert spoken == [orch._cues["listening"], orch._cues["listening"]]


@pytest.mark.asyncio
async def test_no_speech_silent_over_a_stream(tmp_path):
    orch, spoken = _orch(tmp_path, radio_playing=True)  # duck is the feedback there
    await orch._on_envelope("asr", _err("asr.no_speech"))
    assert spoken == []


@pytest.mark.asyncio
async def test_no_text_still_prompts_not_understood(tmp_path):
    # Guard the sibling cue (#50) — distinct code, distinct phrase, no cooldown.
    orch, spoken = _orch(tmp_path)
    await orch._on_envelope("asr", _err("asr.no_text"))
    assert spoken == [orch._cues["not_understood"]]
