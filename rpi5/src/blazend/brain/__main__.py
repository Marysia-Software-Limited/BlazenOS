"""Entrypoint: `python -m blazend.brain` (mock mode for M1)."""

from __future__ import annotations

import argparse
import asyncio
import logging

from blazend.events import Envelope, system_event
from blazend.ipc import Publisher, runtime_dir

log = logging.getLogger("blazend.brain")


async def run(mock: bool) -> None:
    """Bind brain socket; in mock mode emit periodic canned replies."""
    pub = Publisher(runtime_dir() / "brain.sock")
    await pub.bind()
    log.info("brain online at %s", pub._socket_path)  # noqa: SLF001
    await pub.publish(system_event(source="blazend-brain", kind="ready"))
    if not mock:
        await asyncio.Event().wait()
    replies = [
        ("en", "The time is twelve oh three."),
        ("pl", "Jest dwunasta zero trzy."),
        ("en", "You're welcome."),
        ("pl", "Proszę bardzo."),
    ]
    idx = 0
    while True:
        await asyncio.sleep(30)
        lang, text = replies[idx % len(replies)]
        idx += 1
        await pub.publish(
            Envelope(
                topic="brain.reply",
                source="blazend-brain",
                data={"language": lang, "text": text, "chunk": text, "final_": True},
            )
        )
        log.info("emit brain.reply %s %r", lang, text)


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(prog="blazend-brain")
    parser.add_argument("--mock", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    asyncio.run(run(args.mock))


if __name__ == "__main__":
    main()
