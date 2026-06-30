"""Tier 1+ — full-stack nlu arbitration (real binary + real brain + dispatcher).

Spawns the real Rust `blazend-nlu`, runs the Python brain (`serve`) and a
dispatcher consumer (the orchestrator's role) in-process, and drives
`asr.final` through the live stack to prove:

  * a matched **command** ("głośniej") → `nlu.intent` → the dispatcher
    speaks, and the brain stays **silent** (no double-reply);
  * an unmatched **utterance** ("zapamiętaj…") → `nlu.miss` → the brain
    speaks, and the dispatcher stays **silent**.

Skips when `blazend-nlu` isn't built (CI runs `make rust` first).
"""
from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

import pytest

from blazend.config import load as load_config
from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.engine import Assistant
from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.gemini import GeminiClient
from blazend.domains.ai_orchestrator.adapters.rpi5.brain.__main__ import serve
from blazend.domains.ai_orchestrator.adapters.rpi5.dispatch import IntentDispatcher, SettingsStore
from blazend.domains.context.adapters.rpi5.memory import MemoryStore
from blazend.events import Envelope
from blazend.ipc import Publisher, Subscriber

REPO = Path(__file__).resolve().parents[3]
CONFIGS = REPO / "configs"
APP = Path(__file__).resolve().parents[2]


def _nlu_binary() -> Path | None:
    for root in (APP / "crates/target/debug", APP / "crates/target/release"):
        p = root / "blazend-nlu"
        if p.is_file() and os.access(p, os.X_OK):
            return p
    return None


async def _wait_path(p: Path, timeout: float = 10.0) -> None:
    for _ in range(int(timeout / 0.05)):
        if p.exists():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"path never appeared: {p}")


async def _wait(fn, timeout: float = 5.0) -> None:
    for _ in range(int(timeout / 0.05)):
        if fn():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition not met within timeout")


@pytest.mark.asyncio
async def test_pipeline_arbitration(tmp_path, monkeypatch):
    nlu_bin = _nlu_binary()
    if nlu_bin is None:
        pytest.skip("blazend-nlu not built — run `make rust`")

    rt = tmp_path
    monkeypatch.setenv("BLAZEN_CONFIG_ROOT", str(CONFIGS))
    env = {**os.environ, "BLAZEN_RUNTIME_DIR": str(rt), "BLAZEN_CONFIG_ROOT": str(CONFIGS)}

    asr = Publisher(rt / "asr.sock")
    await asr.bind()

    proc = subprocess.Popen([str(nlu_bin)], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    stop = asyncio.Event()
    tasks: list[asyncio.Task] = []
    dispatched: list = []
    brain_replies: list = []
    try:
        await _wait_path(rt / "nlu.sock")

        engine = Assistant(memory=MemoryStore(rt / "mem.json"),
                           gemini=GeminiClient(api_key=""), always_awake=True)
        tasks.append(asyncio.create_task(serve(engine, runtime_dir_=rt, stop=stop)))
        await _wait_path(rt / "brain.sock")

        dispatcher = IntentDispatcher(
            load_config("intents/system").data, load_config("voice-policy").data,
            SettingsStore(rt / "settings.json"),
        )

        async def dispatch_consumer():
            sub = Subscriber(rt / "nlu.sock")
            await sub.connect()
            async for e in sub:
                if e.topic == "nlu.intent":
                    r = dispatcher.dispatch(e.data.get("intent", ""), e.data.get("params", {}),
                                            e.data.get("language", "pl"))
                    if r.speak:
                        dispatched.append(r)

        async def brain_consumer():
            sub = Subscriber(rt / "brain.sock")
            await sub.connect()
            async for e in sub:
                if e.topic == "brain.reply":
                    brain_replies.append(e.data)

        tasks.append(asyncio.create_task(dispatch_consumer()))
        tasks.append(asyncio.create_task(brain_consumer()))
        # Let every subscriber connect (brain+dispatcher→nlu.sock,
        # brain_consumer→brain.sock) and nlu connect to asr.sock.
        await asyncio.sleep(1.2)

        # 1) Matched command → dispatcher only.
        await asr.publish(Envelope(topic="asr.final", source="blazend-asr",
                                   data={"language": "pl", "text": "głośniej", "confidence": 0.9}))
        await _wait(lambda: len(dispatched) >= 1, 5.0)
        assert "Głośność" in dispatched[-1].speak
        await asyncio.sleep(0.6)  # give the brain a chance to (wrongly) reply
        assert brain_replies == [], "brain must not reply to a matched command"

        # 2) A memory command ("zapamiętaj …") is now a fast-path intent → the
        #    dispatcher (via the Phase 4d tool bridge), NOT the brain.
        await asr.publish(Envelope(topic="asr.final", source="blazend-asr",
                                   data={"language": "pl",
                                         "text": "zapamiętaj że kod do bramy to 4729",
                                         "confidence": 0.9}))
        await _wait(lambda: len(dispatched) >= 2, 5.0)
        assert "4729" in dispatched[-1].speak
        await asyncio.sleep(0.6)  # give the brain a chance to (wrongly) reply
        assert brain_replies == [], "brain must not reply to a matched command"

        # 3) A genuine free-chat utterance → brain only.
        await asr.publish(Envelope(topic="asr.final", source="blazend-asr",
                                   data={"language": "pl",
                                         "text": "opowiedz mi coś ciekawego o kosmosie",
                                         "confidence": 0.9}))
        await _wait(lambda: len(brain_replies) >= 1, 5.0)
        assert len(dispatched) == 2, "dispatcher must not fire on an unmatched utterance"
    finally:
        stop.set()
        for t in tasks:
            t.cancel()
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        await asr.close()
