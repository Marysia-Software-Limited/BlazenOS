"""Tier 1 — orchestrator supervisor end-to-end on real Unix sockets.

Spins up the real :class:`Orchestrator`, plays the role of three peers
(wake, asr, health) over our IPC contract, and asserts that the state
file reflects every event we push.

Uses a short tempdir under /tmp to dodge the macOS AF_UNIX path cap.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from pathlib import Path

import pytest

from blazend.domains.systems.adapters.rpi5.orchestrator import Orchestrator
from blazend.events import (
    Envelope,
    system_event,
    wake_detected,
)
from blazend.ipc import Publisher


@pytest.fixture
def runtime_dir():
    """Short tempdir under /tmp for AF_UNIX."""
    d = Path(tempfile.mkdtemp(prefix="bl-or-", dir="/tmp"))
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def _wait_for(predicate, *, timeout: float = 2.0, interval: float = 0.05):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError("predicate never became true")


@pytest.mark.asyncio
async def test_orchestrator_records_three_events(runtime_dir: Path):
    # Peers publish first; orchestrator subscribes lazily.
    peer_wake = Publisher(runtime_dir / "wake.sock")
    peer_health = Publisher(runtime_dir / "health.sock")
    peer_asr = Publisher(runtime_dir / "asr.sock")
    await peer_wake.bind()
    await peer_health.bind()
    await peer_asr.bind()

    orch = Orchestrator(peers=("wake", "health", "asr"), runtime_dir_=runtime_dir)
    task = asyncio.create_task(orch.run())

    # Give the orchestrator a beat to connect.
    await asyncio.sleep(0.4)

    await peer_health.publish(
        system_event(source="blazend-health", kind="heartbeat")
    )
    await peer_wake.publish(
        wake_detected(source="blazend-wake", model="hey_blazen_pl", score=0.81, language="pl")
    )
    await peer_asr.publish(
        Envelope(
            topic="asr.final",
            source="blazend-asr",
            data={"language": "pl", "text": "która godzina", "confidence": 0.93},
        )
    )

    state_path = runtime_dir / "state.json"

    def state_has_pl_wake() -> bool:
        if not state_path.is_file():
            return False
        data = json.loads(state_path.read_text())
        ww = data.get("wake_word") or {}
        units = data.get("units") or {}
        return (
            ww.get("last_fired") == "hey_blazen_pl"
            and ww.get("last_language") == "pl"
            and "wake" in units
            and "asr" in units
            and "health" in units
            and data.get("ready") is True
        )

    await _wait_for(state_has_pl_wake, timeout=2.5)

    snap = json.loads(state_path.read_text())
    assert snap["wake_word"]["last_fired"] == "hey_blazen_pl"
    assert snap["wake_word"]["last_score"] == 0.81
    assert snap["units"]["asr"]["last_topic"] == "asr.final"
    assert snap["ready"] is True

    await orch.shutdown()
    await asyncio.wait_for(task, timeout=2.0)
    await peer_wake.close()
    await peer_health.close()
    await peer_asr.close()


@pytest.mark.asyncio
async def test_prepare_speaker_warms_audio_out_only_when_down(
    runtime_dir: Path, monkeypatch: pytest.MonkeyPatch,
):
    """The pre-roll brings audio-out up (and lets it subscribe to tts.frame) BEFORE a
    reply is published — but only when it was down. A rapid follow-up (already up)
    skips the warm-up, so we don't add latency mid-conversation."""
    from blazend.domains.systems.adapters.rpi5.orchestrator import supervisor as sup
    monkeypatch.setattr(sup, "_SPEAKER_WARMUP_S", 0.01)  # keep the test quick
    orch = Orchestrator(peers=(), runtime_dir_=runtime_dir)
    applied: list[bool] = []

    async def fake_apply(up: bool) -> None:
        applied.append(up)
        orch._audio_out_up = up  # noqa: SLF001

    orch._apply_audio_out = fake_apply  # type: ignore[method-assign]  # noqa: SLF001

    # Idle: audio-out down → warm it up so the (possibly cache-fast) frame isn't lost.
    orch._audio_out_up = False  # noqa: SLF001
    await orch._prepare_speaker(20)  # noqa: SLF001
    assert applied == [True]

    # Already up (a follow-up reply in the same conversation) → no extra warm-up.
    applied.clear()
    await orch._prepare_speaker(20)  # noqa: SLF001
    assert applied == []


@pytest.mark.asyncio
async def test_not_understood_cue_is_rate_limited(runtime_dir: Path):
    """A false-wake storm feeds ambient noise → asr.no_text → the 'Nie zrozumiałam'
    cue. Without a cooldown each false wake speaks it (and churns audio-out); the
    cooldown collapses a burst to a single cue."""
    peer_asr = Publisher(runtime_dir / "asr.sock")
    await peer_asr.bind()

    orch = Orchestrator(peers=("asr",), runtime_dir_=runtime_dir)
    said: list[str] = []

    async def fake_speak(text: str, lang: str = "pl") -> None:
        said.append(text)

    orch._speak = fake_speak  # type: ignore[method-assign]  # noqa: SLF001
    task = asyncio.create_task(orch.run())
    await asyncio.sleep(0.4)  # let it connect to the asr peer
    orch._awake_until = asyncio.get_running_loop().time() + 1000  # stay awake  # noqa: SLF001

    err = Envelope(
        topic="error", source="blazend-asr",
        data={"code": "asr.no_text", "message": "no speech recognised"},
    )
    for _ in range(3):  # three false-wake captures in quick succession
        await peer_asr.publish(err)

    await _wait_for(lambda: len(said) >= 1, timeout=2.0)
    await asyncio.sleep(0.3)  # give any (wrongly) un-throttled extra cues time to land
    assert said == ["Nie zrozumiałam."]  # burst collapsed to one cue

    await orch.shutdown()
    await asyncio.wait_for(task, timeout=2.0)
    await peer_asr.close()


@pytest.mark.asyncio
async def test_orchestrator_survives_missing_peer(runtime_dir: Path):
    """No peers exist — orchestrator should still bind, write initial state,
    and shut down cleanly."""
    orch = Orchestrator(peers=("does-not-exist",), runtime_dir_=runtime_dir)
    task = asyncio.create_task(orch.run())
    await asyncio.sleep(0.3)

    state_path = runtime_dir / "state.json"
    assert state_path.is_file()
    snap = json.loads(state_path.read_text())
    assert snap["v"] == 1
    assert snap.get("ready") is False

    await orch.shutdown()
    await asyncio.wait_for(task, timeout=2.0)


@pytest.mark.asyncio
async def test_thinking_event_speaks_working_cue_once(runtime_dir: Path):
    """The brain's system.event kind=thinking (published just before it blocks on
    the LLM) makes the supervisor speak "Chwileczkę." — announced wait, not dead
    air. The cooldown collapses a burst to one cue per question."""
    peer_brain = Publisher(runtime_dir / "brain.sock")
    await peer_brain.bind()

    orch = Orchestrator(peers=("brain",), runtime_dir_=runtime_dir)
    said: list[str] = []

    async def fake_speak(text: str, lang: str = "pl") -> None:
        said.append(text)

    orch._speak = fake_speak  # type: ignore[method-assign]  # noqa: SLF001
    task = asyncio.create_task(orch.run())
    await asyncio.sleep(0.4)  # let it connect to the brain peer

    thinking = Envelope(
        topic="system.event", source="blazend-brain",
        data={"kind": "thinking"},
    )
    for _ in range(3):  # duplicate events inside one question's window
        await peer_brain.publish(thinking)

    await _wait_for(lambda: len(said) >= 1, timeout=2.0)
    await asyncio.sleep(0.3)
    assert said == ["Chwileczkę."]

    await orch.shutdown()
    await asyncio.wait_for(task, timeout=2.0)
    await peer_brain.close()
