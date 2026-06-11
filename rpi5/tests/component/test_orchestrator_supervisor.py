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

from blazend.events import (
    Envelope,
    system_event,
    wake_detected,
)
from blazend.ipc import Publisher
from blazend.orchestrator import Orchestrator


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
