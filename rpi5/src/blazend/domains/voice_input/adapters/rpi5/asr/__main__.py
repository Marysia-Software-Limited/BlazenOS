"""Entrypoint: `python -m blazend.domains.voice_input.adapters.rpi5.asr`.

Real mode wires the on-device voice path: subscribe to `vad.start`/`vad.end`
from `blazend-audio-in`, read the utterance's PCM from the shared-memory ring
(`blazend.domains.voice_input.adapters.rpi5.audio.RingReader`), transcribe it Polish-first
(`blazend.domains.voice_input.adapters.rpi5.asr.engine.Transcriber` → faster-whisper), and publish `asr.final`
for `blazend-nlu`. `--mock` keeps the M1 synthetic behaviour for CI/laptops.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

import numpy as np

from blazend.config import load
from blazend.domains.voice_input.adapters.rpi5.audio import RingReader
from blazend.events import Envelope, system_event
from blazend.ipc import Publisher, Subscriber, runtime_dir

log = logging.getLogger("blazend.domains.voice_input.adapters.rpi5.asr")

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
    from blazend.domains.voice_input.adapters.rpi5.asr.engine import Transcriber

    rt = runtime_dir()
    ring = await _open_ring(rt / "audio-ring.shm")
    cap_s = float(load("wake-word").get("capture_window_s", 4.5))
    # Noise-floor gate for the post-wake window. TWO floors: idle (no stream) uses
    # the high floor — a clear command is ~400+, ambient false wakes ~100-200, so
    # this drops hallucination-bait when nothing is playing. While a stream plays
    # (speaker-busy marker) the Jabra's DSP attenuates the mic, so a command spoken
    # over the (ducked) radio lands at only ~50-120 — use the low floor so real
    # commands aren't dropped.
    _cfg = load("asr")
    min_rms = float(_cfg.get("min_capture_rms", 200.0))
    min_rms_playing = float(_cfg.get("min_capture_rms_playing", 40.0))
    speaker_busy = rt / "speaker-busy"
    transcriber = Transcriber()
    log.info("asr real path: model=%s ring=%dHz fixed-window=%.1fs",
             transcriber.model, ring.sample_rate, cap_s)

    sub = await _connect(rt / "wake.sock")
    log.info("subscribed to wake.detected on wake.sock (fixed-window capture)")

    # Capture a FIXED window straight from the ring after the wake word — NOT the
    # energy VAD, which fragments quiet short utterances on this mic (it brackets
    # a transient + silence and whisper then finds no speech). The wake ts_ms is
    # the ring write_pos at detection (last_pos*1000/SR in blazend-wake); the
    # spoken command follows it. Wait for the window to fill, then transcribe
    # [wake_pos, wake_pos + capture_window]. Documented design: wake-word.yaml
    # `capture_window_s` + voice/runner.py. (Ring must hold >= capture_window_s.)
    async for env in sub:
        if env.topic != "wake.detected":
            continue
        # Ignore wakes that fire while Jessica is speaking her own reply: the TTS
        # loops from the Jabra speaker into its mic and false-triggers the wake,
        # which would capture Jessica's own voice → endless self-reply loop. The
        # orchestrator sets `speaking` only for the TTS window (NOT for radio), so
        # voice control of a playing stream still works.
        if (rt / "speaking").exists():
            log.info("asr: ignoring wake during self-speech (TTS echo)")
            continue
        wake_pos = int(env.ts_ms) * ring.sample_rate // 1000
        # Skip stale wakes (ASR backed up under a burst): if the window start is
        # already older than the ring holds, its audio is gone.
        pre = 2 * ring.sample_rate  # a bit before the reported wake position
        if ring.write_pos - (wake_pos - pre) > ring.capacity:
            log.warning("asr: dropping stale wake (ring overwritten; backlog)")
            continue
        # Wait for the command to be spoken after the wake word, then read from
        # just-before-wake up to the CURRENT write head — so the window contains
        # the command regardless of the pause length or the wake's report latency.
        await asyncio.sleep(min(cap_s, 3.0))
        end = ring.write_pos
        begin = max(wake_pos - pre, end - ring.capacity + ring.sample_rate)
        pcm = ring.read_range(begin, end)
        if len(pcm) < ring.sample_rate // 2:  # < 0.5 s captured — nothing to do
            continue
        # Capture level: gate on the PEAK over ~200 ms frames, not the whole-window
        # average. A short command ("ciszej"/"stop"/"inny") is ~0.4 s of speech in a
        # ~3 s window, so its average is low (~120) even though the word peaks loud
        # (~600+); the old average gate dropped it as a false wake. A true false wake
        # (uniform ambient / the mic's noise floor) stays low in EVERY frame (peak
        # ~100-150), so peak still rejects it. Averages logged for calibration.
        arr = np.asarray(pcm, dtype=np.float64)
        rms = float(np.sqrt((arr ** 2).mean())) if len(arr) else 0.0
        frame = max(1, ring.sample_rate // 5)  # 200 ms
        peak = 0.0
        for i in range(0, len(arr) - frame + 1, frame):
            peak = max(peak, float(np.sqrt((arr[i : i + frame] ** 2).mean())))
        peak = max(peak, rms)  # short-window guard: never below the whole-window RMS
        log.info("fixed-window read %.1fs rms=%.0f peak=%.0f", len(pcm) / ring.sample_rate, rms, peak)
        # Noise-floor gate (lower while a stream plays → the Jabra ducks the mic).
        floor = min_rms_playing if speaker_busy.exists() else min_rms
        if peak < floor:
            log.info("fixed-window peak=%.0f below floor %.0f — dropping (false wake)", peak, floor)
            continue
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
            # Passed the level gate but whisper found no words (too quiet/garbled,
            # or a non-speech peak like a cough). Log it — otherwise the trace goes
            # silent after "read" and the drop looks like a hang.
            log.info("asr: no speech recognised (whisper empty) — rms=%.0f peak=%.0f", rms, peak)
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
