"""Tier 1 — the brain unit driving the real assistant engine over IPC.

Binds a stand-in ASR publisher, runs `blazend.brain.serve()` against a
keyless engine (so memory commands are deterministic, no network), and
asserts that an `asr.final` produces the matching `brain.reply`.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from blazend.assistant.engine import Assistant
from blazend.assistant.gemini import GeminiClient
from blazend.assistant.memory import MemoryStore
from blazend.brain.__main__ import serve
from blazend.events import Envelope
from blazend.ipc import Publisher, Subscriber


async def _connect(path, *, tries: int = 40) -> Subscriber:
    for _ in range(tries):
        if path.exists():
            try:
                sub = Subscriber(path)
                await sub.connect()
                return sub
            except (FileNotFoundError, ConnectionRefusedError, OSError):
                pass
        await asyncio.sleep(0.05)
    raise AssertionError(f"socket never appeared: {path}")


@pytest.mark.asyncio
async def test_asr_final_routes_through_brain(tmp_path):
    rt = tmp_path
    engine = Assistant(
        memory=MemoryStore(tmp_path / "mem.json"),
        gemini=GeminiClient(api_key=""),  # keyless → deterministic offline paths
        always_awake=True,
    )

    asr = Publisher(rt / "asr.sock")
    await asr.bind()

    stop = asyncio.Event()
    server = asyncio.create_task(serve(engine, runtime_dir_=rt, stop=stop))
    try:
        # Let serve() bind brain.sock and connect to asr.sock.
        brain_sub = await _connect(rt / "brain.sock")
        await asyncio.sleep(0.3)

        # A memory command → deterministic note ack (no Gemini needed).
        await asr.publish(Envelope(
            topic="asr.final", source="blazend-asr",
            data={"language": "pl", "text": "zapamiętaj że kod do bramy to 4729", "confidence": 0.9},
        ))
        env = await asyncio.wait_for(brain_sub.next(), timeout=3.0)
        assert env.topic == "brain.reply"
        assert env.data["action"] == "note"
        assert "4729" in env.data["text"]

        # A reminder fires through the ticker as a brain.reply.
        await asr.publish(Envelope(
            topic="asr.final", source="blazend-asr",
            data={"language": "pl", "text": "przypomnij mi o herbacie za 1 sekundę", "confidence": 0.9},
        ))
        ack = await asyncio.wait_for(brain_sub.next(), timeout=3.0)
        assert ack.data["action"] == "reminder"
        fired = await asyncio.wait_for(brain_sub.next(), timeout=4.0)
        assert "herbacie" in fired.data["text"] and fired.data.get("action") == "reminder"
    finally:
        stop.set()
        await asyncio.wait_for(server, timeout=3.0)
        await asr.close()
