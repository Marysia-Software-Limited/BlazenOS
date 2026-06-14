"""Entrypoint: `python -m blazend.asr` (mock mode for M1)."""

from __future__ import annotations

import argparse
import asyncio
import logging

from blazend.events import Envelope, system_event
from blazend.ipc import Publisher, runtime_dir

log = logging.getLogger("blazend.asr")

MOCK_UTTERANCES = [
    ("pl", "która godzina"),
    ("en", "hey blazen what time is it"),
    ("pl", "dziękuję"),
    ("en", "thanks"),
]


async def run(mock: bool) -> None:
    """Bind socket; emit synthetic asr.final in mock mode."""
    pub = Publisher(runtime_dir() / "asr.sock")
    await pub.bind()
    log.info("asr online at %s", pub._socket_path)  # noqa: SLF001
    await pub.publish(system_event(source="blazend-asr", kind="ready"))
    if not mock:
        await asyncio.Event().wait()
    idx = 0
    while True:
        await asyncio.sleep(30)
        lang, text = MOCK_UTTERANCES[idx % len(MOCK_UTTERANCES)]
        idx += 1
        env = Envelope(
            topic="asr.final",
            source="blazend-asr",
            data={"language": lang, "text": text, "confidence": 0.91},
        )
        await pub.publish(env)
        log.info("emit asr.final %s %r", lang, text)


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(prog="blazend-asr")
    parser.add_argument("--mock", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    asyncio.run(run(args.mock))


if __name__ == "__main__":
    main()
