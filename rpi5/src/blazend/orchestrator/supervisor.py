"""The orchestrator: connects to every peer socket, tracks state, reacts.

M1 scope:
  - Open a publisher socket (`orchestrator.sock`).
  - Subscribe to wake, audio-in, asr, brain, tts, audio-out, health.
  - For every received envelope, merge a small summary into state.json.
  - Re-publish a `system.event` `ready` after first heartbeat from health.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from blazend.events import Envelope, system_event
from blazend.ipc import Publisher, Subscriber, runtime_dir
from blazend.led import LedSimulator
from blazend.state import StateWriter

log = logging.getLogger("blazend.orchestrator")

DEFAULT_PEERS: tuple[str, ...] = (
    "audio-in",
    "wake",
    "asr",
    "nlu",
    "brain",
    "tts",
    "audio-out",
    "health",
)


class Orchestrator:
    """The supervisor process. One per system."""

    def __init__(
        self,
        peers: Iterable[str] = DEFAULT_PEERS,
        runtime_dir_: Path | None = None,
    ):
        self._runtime_dir = runtime_dir_ or runtime_dir()
        self._peers = tuple(peers)
        self._state = StateWriter(self._runtime_dir / "state.json")
        self._led = LedSimulator(self._runtime_dir / "led.json")
        self._publisher = Publisher(self._runtime_dir / "orchestrator.sock")
        self._stop = asyncio.Event()
        self._subscribers: list[tuple[str, Subscriber]] = []

    async def run(self) -> None:
        """Bind sockets, connect to peers, react until interrupted."""
        await self._publisher.bind()
        await self._state.update({"v": 1, "ready": False, "units": {}})
        self._led.write()  # initial state (off)
        log.info("orchestrator bound at %s", self._publisher._socket_path)  # noqa: SLF001

        # Connect to every peer (idempotent; missing peers are retried lazily).
        for name in self._peers:
            asyncio.create_task(self._peer_loop(name))

        await self._stop.wait()
        await self._publisher.close()
        for _, sub in self._subscribers:
            await sub.close()

    async def shutdown(self) -> None:
        """Signal :meth:`run` to return."""
        self._stop.set()

    async def _peer_loop(self, name: str) -> None:
        socket_path = self._runtime_dir / f"{name}.sock"
        backoff = 1.0
        while not self._stop.is_set():
            if not socket_path.exists():
                await asyncio.sleep(backoff)
                backoff = min(backoff * 1.5, 5.0)
                continue
            try:
                sub = Subscriber(socket_path)
                await sub.connect()
                self._subscribers.append((name, sub))
                log.info("connected to %s", name)
                backoff = 1.0
                await self._consume(name, sub)
            except (FileNotFoundError, ConnectionRefusedError, OSError) as exc:
                log.debug("%s not ready (%s); retrying", name, exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 1.5, 5.0)

    async def _consume(self, name: str, subscriber: Subscriber) -> None:
        async for envelope in subscriber:
            await self._on_envelope(name, envelope)

    async def _on_envelope(self, peer: str, env: Envelope) -> None:
        patch: dict[str, Any] = {
            "units": {peer: {"last_topic": env.topic, "last_ts_ms": env.ts_ms}},
        }
        if env.topic == "system.event" and env.data.get("kind") == "heartbeat":
            patch["ready"] = True
            patch["units"][peer]["status"] = "running"
        if env.topic == "wake.detected":
            patch["wake_word"] = {
                "last_fired": env.data.get("model"),
                "last_language": env.data.get("language"),
                "last_score": env.data.get("score"),
            }
        if self._led.observe(env.topic, env.data):
            self._led.write()
            patch["led"] = self._led.color
        await self._state.update(patch)
        await self._publisher.publish(
            system_event(source="blazend-orchestrator", kind="observed", detail=env.topic)
        )


__all__ = ["Orchestrator"]
