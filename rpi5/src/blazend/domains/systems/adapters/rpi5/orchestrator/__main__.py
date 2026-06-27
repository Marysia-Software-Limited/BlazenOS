"""Entrypoint: `python -m blazend.domains.systems.adapters.rpi5.orchestrator`."""

from __future__ import annotations

import asyncio
import logging
import signal

from .supervisor import Orchestrator


def main() -> None:
    """Run the orchestrator until SIGINT/SIGTERM."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    orchestrator = Orchestrator()

    async def runner() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda: asyncio.create_task(orchestrator.shutdown()))
            except NotImplementedError:
                pass  # Windows / restricted envs
        await orchestrator.run()

    asyncio.run(runner())


if __name__ == "__main__":
    main()
