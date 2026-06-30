"""Tool server (Phase 4d ML/API glue).

Entrypoint: ``python -m blazend.domains.ai_orchestrator.adapters.rpi5.tool_server``.

Subscribes to ``tool.request`` (from the Rust dispatch in ``blazend-mind``),
runs the named tool via [`Tools`], and publishes ``tool.response`` with the same
``request_id``. The mind speaks the response (re-emitting it as ``brain.reply``)
and applies any side effect (e.g. starting a radio stream).

Tools are API/ML/hardware glue and stay Python; only the *recognition* and
*orchestration* are Rust. See ``docs/14-RUST-PYTHON-SPLIT.md`` §1.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path
from typing import Any

from blazend.domains.ai_orchestrator.adapters.rpi5.tools import Tools
from blazend.events import TOPIC_TOOL_REQUEST, TOPIC_TOOL_RESPONSE, Envelope, system_event
from blazend.ipc import Publisher, Subscriber, runtime_dir

log = logging.getLogger("blazend.domains.ai_orchestrator.tool_server")


class ToolService:
    """Turns a ``tool.request`` envelope into a ``tool.response`` envelope."""

    def __init__(self, tools: Tools | None = None) -> None:
        self.tools = tools if tools is not None else Tools()

    def response_for(self, env: Envelope) -> Envelope:
        d = env.data
        rid = str(d.get("request_id", ""))
        lang = str(d.get("language", "pl"))
        args: dict[str, Any] = d.get("args") or {}
        res = self.tools.run(str(d.get("tool", "")), args, lang)
        return Envelope(
            topic=TOPIC_TOOL_RESPONSE,
            source="blazend-tools",
            data={
                "request_id": rid,
                "ok": res.ok,
                "text": res.text,
                "language": lang,
                "action": res.action,
                "payload": res.payload,
            },
        )


async def _connect(sock: Path) -> Subscriber:
    """Connect to the tool-request publisher (the mind), retrying."""
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
    service: ToolService | None = None,
    *,
    runtime_dir_: Path | None = None,
    request_sock: str = "mind.sock",
    stop: asyncio.Event | None = None,
) -> None:
    """Serve ``tool.request`` → ``tool.response`` until ``stop`` (or EOF)."""
    rt = runtime_dir_ or runtime_dir()
    srv = service or ToolService()
    pub = Publisher(rt / "tools.sock")
    await pub.bind()
    await pub.publish(system_event(source="blazend-tools", kind="ready"))
    sub = await _connect(rt / request_sock)
    log.info("tool server online (tool.request → tool.response)")
    while stop is None or not stop.is_set():
        env = await sub.next()
        if env is None:
            break
        if env.topic != TOPIC_TOOL_REQUEST:
            continue
        reply = await asyncio.to_thread(srv.response_for, env)
        await pub.publish(reply)


def main() -> None:
    """CLI entrypoint."""
    logging.basicConfig(level=logging.INFO)
    argparse.ArgumentParser(prog="blazend-tools").parse_args()
    asyncio.run(run())


if __name__ == "__main__":
    main()
