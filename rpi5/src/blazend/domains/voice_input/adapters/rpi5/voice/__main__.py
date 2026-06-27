"""Entrypoint: ``python -m blazend.domains.voice_input.adapters.rpi5.voice`` — the hands-free "Hej Jessico" loop.

Starts the two Rust units that own the mic (`blazend-audio-in`, `blazend-wake`)
out of band (see `scripts/voice-run.sh`); this process reads the ring + the
`wake.detected` socket they expose, and runs ASR → engine → Piper itself. The
HAT button (GPIO line) is a push-to-talk fallback that delimits its own window.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import threading
from pathlib import Path

import numpy as np
import numpy.typing as npt

from blazend.config import load
from blazend.domains.voice_input.adapters.rpi5.audio import RingReader
from blazend.domains.voice_input.adapters.rpi5.voice.runner import (
    PiperSink,
    RingCapturer,
    StreamPlayer,
    build_runner,
)
from blazend.ipc import Subscriber, runtime_dir

log = logging.getLogger("blazend.domains.voice_input.adapters.rpi5.voice")

REPO = Path(__file__).resolve().parents[4]
_GPIO_CHIP = os.environ.get("BLAZEN_BUTTON_CHIP", "gpiochip0")
_GPIO_LINE = os.environ.get("BLAZEN_BUTTON_LINE", "17")


def _models_root() -> Path:
    return Path(os.environ.get("BLAZEN_MODELS_DIR", str(REPO / "models")))


def _voices() -> dict[str, str]:
    """Map language → Piper voice .onnx path, from `tts.yaml`."""
    cfg = load("tts")
    by_lang = cfg.get("voice_by_language", {}) or {}
    tts_dir = _models_root() / "tts"
    voices: dict[str, str] = {}
    for lang in ("pl", "en"):
        name = by_lang.get(lang, "pl_PL-gosia-medium" if lang == "pl" else "en_US-lessac-medium")
        voices[lang] = str(tts_dir / f"{name}.onnx")
    return voices


def _wait_edge(edge: str) -> None:
    subprocess.run(
        ["gpiomon", "-c", _GPIO_CHIP, "--bias=pull-up", "-e", edge, "-n", "1", _GPIO_LINE],
        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _button_loop(
    ring: RingReader,
    loop: asyncio.AbstractEventLoop,
    queue: asyncio.Queue[npt.NDArray[np.int16]],
    stop: threading.Event,
) -> None:
    """Push-to-talk fallback: capture the held window straight from the ring."""
    while not stop.is_set():
        _wait_edge("falling")
        start = ring.write_pos
        _wait_edge("rising")
        pcm = ring.read_range(start, ring.write_pos)
        loop.call_soon_threadsafe(queue.put_nowait, pcm)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    rt = runtime_dir()
    piper = os.environ.get("BLAZEN_PIPER", str(REPO / ".venv/bin/piper"))
    out = os.environ.get("PTT_OUT", "plughw:CARD=wm8960soundcard,DEV=0")

    ring = RingReader(rt / "audio-ring.shm")
    capturer = RingCapturer(ring)
    sink = PiperSink(piper=piper, voices=_voices(), out_device=out)
    radio = StreamPlayer(
        device=out,
        player=os.environ.get("BLAZEN_PLAYER", str(REPO / "rpi5/crates/target/debug/blazend-player")),
    )
    runner = build_runner(capturer=capturer, sink=sink, radio=radio)

    sub = Subscriber(rt / "wake.sock")
    await sub.connect()

    queue: asyncio.Queue[npt.NDArray[np.int16]] = asyncio.Queue()
    stop_thread = threading.Event()
    loop = asyncio.get_running_loop()
    if os.environ.get("BLAZEN_BUTTON", "1") != "0":
        threading.Thread(
            target=_button_loop, args=(ring, loop, queue, stop_thread), daemon=True
        ).start()

    stop = asyncio.Event()
    log.info("WAKE-READY  capture=%.1fs  out=%s — say 'Hej Jessico' or hold the button.",
             runner.capture_s, out)
    try:
        await runner.run(sub, stop=stop, triggers=queue)
    finally:
        stop_thread.set()
        runner.radio.stop()
        runner.status_led.close()
        await sub.close()
        ring.close()


if __name__ == "__main__":
    asyncio.run(main())
