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

from blazend.config import load as load_config
from blazend.dispatch import IntentDispatcher, SettingsStore
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
        dispatcher: IntentDispatcher | None = None,
    ):
        self._runtime_dir = runtime_dir_ or runtime_dir()
        self._peers = tuple(peers)
        self._state = StateWriter(self._runtime_dir / "state.json")
        self._led = LedSimulator(self._runtime_dir / "led.json")
        self._publisher = Publisher(self._runtime_dir / "orchestrator.sock")
        self._dispatcher = dispatcher if dispatcher is not None else self._build_dispatcher()
        self._stop = asyncio.Event()
        self._subscribers: list[tuple[str, Subscriber]] = []

    def _build_dispatcher(self) -> IntentDispatcher | None:
        """Load intents + voice-policy for fast-path command dispatch.

        Returns ``None`` (commands just observed) if the configs aren't
        reachable — e.g. a bare test runtime with no ``BLAZEN_CONFIG_ROOT``.
        """
        try:
            intents = load_config("intents/system").data
            policy = load_config("voice-policy").data
            if not intents.get("intents"):
                return None
            settings = SettingsStore(self._runtime_dir / "settings.json")
            return IntentDispatcher(intents, policy, settings)
        except Exception as exc:  # noqa: BLE001
            log.warning("intent dispatcher disabled (%s)", exc)
            return None

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
        if env.topic == "nlu.intent" and self._dispatcher is not None:
            reply = self._dispatch_intent(env)
            if reply is not None:
                await self._publisher.publish(reply)
                patch["last_command"] = {
                    "intent": env.data.get("intent"),
                    "result": reply.data.get("action"),
                }
            # Keep the language pin authoritative in state (scenario 09).
            patch["languages"] = {"pinned": self._dispatcher.pinned_language()}
        if self._led.observe(env.topic, env.data):
            self._led.write()
            patch["led"] = self._led.color
        await self._state.update(patch)
        await self._publisher.publish(
            system_event(source="blazend-orchestrator", kind="observed", detail=env.topic)
        )

    def _dispatch_intent(self, env: Envelope) -> Envelope | None:
        """Act on a fast-path `nlu.intent`; return the spoken reply (if any)."""
        result = self._dispatcher.dispatch(
            env.data.get("intent", ""),
            env.data.get("params", {}),
            env.data.get("language", "pl"),
        )
        if result.signal in ("reboot", "shutdown"):
            # In the VM / dev we never actually power off; on the device a
            # power unit consumes this. Log it loudly.
            log.warning("system command requested: %s (not executed in dev)", result.signal)
        if not result.speak:
            return None
        return Envelope(
            topic="brain.reply",
            source="blazend-orchestrator",
            data={
                "language": result.language,
                "text": result.speak,
                "chunk": result.speak,
                "final_": True,
                "action": f"command.{result.action}",
            },
        )


__all__ = ["Orchestrator"]
