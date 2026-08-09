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


def _fake_aplay(monkeypatch, observed: dict):
    """Replace the aplay subprocess; record marker state at 'playback' time."""

    async def _spawn(*_args, **_kwargs):
        class _Proc:
            async def communicate(self, _wav):
                observed["during"] = sorted(
                    p.name for p in observed["rt"].iterdir() if p.is_file())
                return b"", b""

        return _Proc()

    monkeypatch.setattr("asyncio.create_subprocess_exec", _spawn)


@pytest.mark.asyncio
async def test_beep_never_touches_the_speaking_marker(tmp_path, monkeypatch):
    """Regression (2026-08-09, deaf pipeline): the wake chime marked itself as
    `speaking`, and the ASR — which drops any wake fired while that marker
    exists (TTS-echo guard) — saw it a millisecond after wake.detected and
    ignored EVERY wake. Beeps must use their own `cue` marker."""
    orch, _ = _orch(tmp_path)
    observed: dict = {"rt": tmp_path}
    _fake_aplay(monkeypatch, observed)
    await orch._play_beep(orch._beep_wav, tail_s=0)
    assert "cue" in observed["during"]
    assert "speaking" not in observed["during"]
    assert not (tmp_path / "cue").exists()  # cleaned up after the tail


@pytest.mark.asyncio
async def test_wake_with_chime_leaves_asr_able_to_listen(tmp_path, monkeypatch):
    """Flow-level guard: after handling wake.detected (chime armed), the
    `speaking` marker must be gone and the `activate` window open — otherwise
    the ASR treats the wake as self-speech and the command is never captured."""
    orch, _ = _orch(tmp_path)
    observed: dict = {"rt": tmp_path}
    _fake_aplay(monkeypatch, observed)

    async def _noop(*_a, **_k):
        return None

    orch._publisher = types.SimpleNamespace(publish=_noop)  # type: ignore[assignment]
    orch._state = types.SimpleNamespace(update=_noop)  # type: ignore[assignment]
    env = Envelope(topic="wake.detected", source="blazend-wake",
                   data={"score": 0.99, "model": "dżesika", "language": "pl"})
    try:
        await orch._on_envelope("wake", env)
    finally:
        orch._cancel_heartbeat()
    assert observed.get("during") is not None  # the chime actually played
    # The instant the chime plays is when the ASR's self-speech check runs.
    assert "speaking" not in observed["during"]
    assert not (tmp_path / "speaking").exists()
    assert (tmp_path / "activate").exists()
