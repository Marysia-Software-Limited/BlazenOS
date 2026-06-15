"""Entrypoint: `python -m blazend.asr`.

Real mode wires the on-device voice path: subscribe to `vad.start`/`vad.end`
from `blazend-audio-in`, read the utterance's PCM from the shared-memory ring
(`blazend.audio.RingReader`), transcribe it Polish-first
(`blazend.asr.engine.Transcriber` → faster-whisper), and publish `asr.final`
for `blazend-nlu`. `--mock` keeps the M1 synthetic behaviour for CI/laptops.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from blazend.audio import RingReader
from blazend.config import load
from blazend.events import Envelope, system_event
from blazend.ipc import Publisher, Subscriber, runtime_dir

log = logging.getLogger("blazend.asr")

MOCK_UTTERANCES = [
    ("pl", "która godzina"),
    ("en", "hey blazen what time is it"),
    ("pl", "dziękuję"),
    ("en", "thanks"),
]


async def _mock_loop(pub: Publisher) -> None:
    idx = 0
    while True:
        await asyncio.sleep(30)
        lang, text = MOCK_UTTERANCES[idx % len(MOCK_UTTERANCES)]
        idx += 1
        await pub.publish(
            Envelope(
                topic="asr.final",
                source="blazend-asr",
                data={"language": lang, "text": text, "confidence": 0.91},
            )
        )
        log.info("emit asr.final %s %r", lang, text)


async def _open_ring(path: Path, timeout: float = 30.0) -> RingReader:
    """Wait for the Rust capture unit to create the ring, then open it."""
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        if path.exists():
            try:
                return RingReader(path)
            except (ValueError, OSError):
                pass
        if asyncio.get_event_loop().time() > deadline:
            raise TimeoutError(f"audio ring {path} never appeared")
        await asyncio.sleep(0.2)


async def _connect(sock: Path) -> Subscriber:
    """Connect to the audio-in publisher, retrying until it binds."""
    while True:
        if sock.exists():
            try:
                sub = Subscriber(sock)
                await sub.connect()
                return sub
            except (FileNotFoundError, ConnectionRefusedError, OSError):
                pass
        await asyncio.sleep(0.2)


async def _real_loop(pub: Publisher) -> None:
    from blazend.asr.engine import Transcriber

    rt = runtime_dir()
    ring = await _open_ring(rt / "audio-ring.shm")
    pre_roll_ms = int(load("audio").get("input.pre_roll_ms", 500))
    pre_roll_frames = pre_roll_ms * ring.sample_rate // 1000
    transcriber = Transcriber()
    log.info("asr real path: model=%s ring=%dHz", transcriber.model, ring.sample_rate)

    sub = await _connect(rt / "audio-in.sock")
    log.info("subscribed to vad events on audio-in.sock")

    start_pos: int | None = None
    async for env in sub:
        if env.topic == "vad.start":
            start_pos = max(0, ring.write_pos - pre_roll_frames)
        elif env.topic == "vad.end":
            if start_pos is None:
                continue
            pcm = ring.read_range(start_pos, ring.write_pos)
            start_pos = None
            result = await asyncio.to_thread(transcriber.transcribe, pcm, ring.sample_rate)
            if result.text:
                await pub.publish(
                    Envelope(
                        topic="asr.final",
                        source="blazend-asr",
                        data={
                            "language": result.language,
                            "text": result.text,
                            "confidence": round(result.confidence, 3),
                        },
                    )
                )
                log.info(
                    "asr.final %s %r (conf=%.2f)", result.language, result.text, result.confidence
                )
            else:
                await pub.publish(
                    Envelope(
                        topic="error",
                        source="blazend-asr",
                        data={"code": "asr.no_text", "message": "no speech recognised"},
                    )
                )


async def run(mock: bool) -> None:
    """Bind asr.sock; mock emits synthetic finals, real transcribes the mic."""
    pub = Publisher(runtime_dir() / "asr.sock")
    await pub.bind()
    log.info("asr online at %s", pub._socket_path)  # noqa: SLF001
    await pub.publish(system_event(source="blazend-asr", kind="ready"))
    if mock:
        await _mock_loop(pub)
    else:
        await _real_loop(pub)


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(prog="blazend-asr")
    parser.add_argument("--mock", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    asyncio.run(run(args.mock))


if __name__ == "__main__":
    main()
