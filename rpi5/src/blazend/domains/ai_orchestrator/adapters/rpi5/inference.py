"""Thin LLM inference server (Phase 4 ML glue).

Entrypoint: ``python -m blazend.domains.ai_orchestrator.adapters.rpi5.inference``.

The **ML-glue half** of the conversation, split out of the old Python engine:
it does *no* routing, memory, or NLU. It subscribes to ``brain.request`` (from
the Rust ``blazend-mind``), runs the model — honouring the mind's ``backend``
hint, then escalating local → OpenAI → Gemini exactly like the old engine — and
publishes ``brain.reply`` carrying the same ``request_id`` (spoken by
``blazend-tts``, observed by the mind).

This is the Python side of the Phase 4 mind/ML-glue split. See
``docs/14-RUST-PYTHON-SPLIT.md`` §1-2 and ``docs/19-DOMAIN-ARCHITECTURE.md``.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from collections.abc import Callable
from pathlib import Path

from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.gemini import (
    GeminiClient,
    GeminiError,
)
from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.openai import (
    OpenAiClient,
    OpenAiError,
)
from blazend.domains.ai_orchestrator.core.registry import select_chat_llm
from blazend.domains.local_ai.core.ports import LlmError, LlmPort
from blazend.events import (
    TOPIC_BRAIN_REPLY,
    TOPIC_BRAIN_REQUEST,
    Envelope,
    system_event,
)
from blazend.ipc import Publisher, Subscriber, runtime_dir

log = logging.getLogger("blazend.domains.ai_orchestrator.inference")


def _t(lang: str, pl: str, en: str) -> str:
    return pl if lang == "pl" else en


class InferenceServer:
    """Runs the model for a ``brain.request`` and answers with ``brain.reply``.

    Backends are built once and reused. ``primary`` is the on-device/LAN chat
    model (``registry.select_chat_llm`` — Bielik, or the dev Ollama box); the
    cloud tiers are the escalation fallback.
    """

    def __init__(
        self,
        *,
        primary: LlmPort | None = None,
        openai: OpenAiClient | None = None,
        gemini: GeminiClient | None = None,
    ) -> None:
        self.primary = primary if primary is not None else select_chat_llm()
        self.openai = openai if openai is not None else OpenAiClient()
        self.gemini = gemini if gemini is not None else GeminiClient()

    def infer(
        self, *, prompt: str, system: str | None, backend: str | None, lang: str
    ) -> str:
        """Answer ``prompt`` with the hinted backend, escalating on miss/error.

        Mirrors the old engine's chain: on-device first (offline, private),
        then OpenAI, then Gemini, then a canned "needs a model/key" reply. The
        ``backend`` hint (from the mind's ``RoutePlan``) just reorders the
        candidates so the chosen tier is tried first.
        """

        def local() -> str | None:
            if self.primary is not None and self.primary.available:
                return self.primary.chat(prompt, system=system)
            return None

        def openai() -> str | None:
            return self.openai.chat(prompt, system=system) if self.openai.available else None

        def gemini() -> str | None:
            return self.gemini.chat(prompt, system=system) if self.gemini.available else None

        orders: dict[str, list[tuple[str, Callable[[], str | None]]]] = {
            "openai": [("openai", openai), ("local", local), ("gemini", gemini)],
            "gemini": [("gemini", gemini), ("local", local), ("openai", openai)],
        }
        # local / ollama / unknown / None → on-device first.
        order = orders.get(
            backend or "",
            [("local", local), ("openai", openai), ("gemini", gemini)],
        )

        for name, fn in order:
            try:
                answer = fn()
            except (LlmError, OpenAiError, GeminiError) as exc:
                log.warning("inference backend %s failed: %s", name, exc)
                continue
            if answer:
                log.info("answered via %s", name)
                return str(answer)
        return _t(
            lang,
            "Mogę rozmawiać swobodnie po ustawieniu lokalnego modelu albo klucza API.",
            "I can chat freely once a local model or an API key is set.",
        )

    def reply_for(self, env: Envelope) -> Envelope:
        """Build the ``brain.reply`` envelope for a ``brain.request``."""
        d = env.data
        rid = str(d.get("request_id", ""))
        lang = str(d.get("language", "pl"))
        text = self.infer(
            prompt=str(d.get("prompt", "")),
            system=d.get("system"),
            backend=d.get("backend"),
            lang=lang,
        )
        return Envelope(
            topic=TOPIC_BRAIN_REPLY,
            source="blazend-brain",
            data={
                "request_id": rid,
                "language": lang,
                "text": text,
                "chunk": text,
                "final_": True,
            },
        )


async def _connect_mind(sock: Path) -> Subscriber:
    """Connect to the mind publisher (``brain.request``), retrying."""
    while True:
        if sock.exists():
            try:
                sub = Subscriber(sock)
                await sub.connect()
                return sub
            except (FileNotFoundError, ConnectionRefusedError, OSError):
                pass
        await asyncio.sleep(0.2)


async def run(
    server: InferenceServer | None = None,
    *,
    runtime_dir_: Path | None = None,
    stop: asyncio.Event | None = None,
) -> None:
    """Serve ``brain.request`` → ``brain.reply`` until ``stop`` (or EOF)."""
    rt = runtime_dir_ or runtime_dir()
    srv = server or InferenceServer()
    pub = Publisher(rt / "brain.sock")
    await pub.bind()
    await pub.publish(system_event(source="blazend-brain", kind="ready"))
    sub = await _connect_mind(rt / "mind.sock")
    log.info("inference server online (brain.request → brain.reply)")
    while stop is None or not stop.is_set():
        env = await sub.next()
        if env is None:
            break
        if env.topic != TOPIC_BRAIN_REQUEST:
            continue
        reply = await asyncio.to_thread(srv.reply_for, env)
        await pub.publish(reply)


def main() -> None:
    """CLI entrypoint."""
    logging.basicConfig(level=logging.INFO)
    argparse.ArgumentParser(prog="blazend-inference").parse_args()
    asyncio.run(run())


if __name__ == "__main__":
    main()
