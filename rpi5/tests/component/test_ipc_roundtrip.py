"""Tier 1 — Python IPC pub/sub round-trip + envelope wire format."""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path

import pytest

from blazend.events import (
    Envelope,
    error_event,
    system_event,
    wake_detected,
)
from blazend.ipc import Publisher, Subscriber


@pytest.fixture
def short_tmp_path():
    """A short tempdir under /tmp to dodge the macOS AF_UNIX path cap (~104)."""
    d = Path(tempfile.mkdtemp(prefix="bl-", dir="/tmp"))
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


@pytest.mark.asyncio
async def test_pub_sub_single_message(short_tmp_path: Path):
    sock = short_tmp_path / "t.sock"
    pub = Publisher(sock)
    await pub.bind()

    sub = Subscriber(sock)
    await sub.connect()
    await asyncio.sleep(0.1)  # give the server time to register the connection

    await pub.publish(system_event(source="t", kind="ready"))
    await asyncio.sleep(0.05)

    received = []

    async def consume():
        async for env in sub:
            received.append(env)
            if len(received) >= 1:
                break

    await asyncio.wait_for(consume(), timeout=1.0)
    await sub.close()
    await pub.close()

    assert len(received) == 1
    assert received[0].topic == "system.event"
    assert received[0].data["kind"] == "ready"


@pytest.mark.asyncio
async def test_envelope_wire_roundtrip():
    env = wake_detected(source="blazend-wake", model="hey_blazen_pl", score=0.84, language="pl")
    wire = env.to_wire()
    assert wire["v"] == 1
    assert wire["topic"] == "wake.detected"
    assert wire["data"]["language"] == "pl"
    back = Envelope.from_wire(wire)
    assert back.topic == env.topic
    assert back.source == env.source
    assert back.data == env.data


def test_error_event_carries_hint():
    env = error_event(source="t", code="brain.timeout", message="stuck", hint="restart me")
    assert env.data == {"code": "brain.timeout", "message": "stuck", "hint": "restart me"}
