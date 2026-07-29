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
import json
import logging
import wave
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from blazend.config import load
from blazend.domains.context.adapters.rpi5.memory import data_dir
from blazend.domains.voice_input.adapters.rpi5.audio import RingReader
from blazend.events import Envelope, system_event
from blazend.ipc import Publisher, Subscriber, runtime_dir

log = logging.getLogger("blazend.domains.voice_input.adapters.rpi5.asr")

# Self-harvesting wake negatives: every false wake whose capture window yields no
# command IS a recording of the exact sound that fooled the wake model — save it so
# the next `train-wake.py --neg-dir` retrain learns from every false activation.
# Clips are screened at retrain time, NOT auto-ingested: a real but too-far/too-quiet
# "dżesika" lands in the same branches, and blindly training on it would teach the
# model to reject the user's own distant voice.
_HARVEST_DIR = Path("/var/lib/blazen/wake-negatives")
_HARVEST_KEEP = 200  # newest clips kept; ~160 KB each → ~32 MB cap

# Rolling utterance clips: every SUCCESSFULLY transcribed post-wake window is
# kept briefly (audio + its text in last.json) so a memory command spoken in the
# same breath — "zapamiętaj, że …" — can claim its own recording and store the
# memory as sound + text. Unclaimed clips just age out of the ring; nothing
# leaves the device.
_CLIPS_KEEP = 50


def _write_wav(pcm: np.typing.ArrayLike, sample_rate: int, path: Path) -> None:
    """Write mono 16-bit PCM as a wav file."""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(np.asarray(pcm, dtype=np.int16).tobytes())


def _save_false_wake(pcm: np.typing.ArrayLike, sample_rate: int, kind: str,
                     root: Path = _HARVEST_DIR) -> Path | None:
    """Persist a failed capture window as a mono 16-bit wav (best-effort: harvesting
    must never break the voice path). ``kind`` tags WHY it failed — ``empty`` (below
    the level floor: quiet ambient) vs ``notext`` (audio present, whisper found no
    words: TV/coughs, but possibly garbled real speech). Prunes to the newest
    ``_HARVEST_KEEP`` files."""
    try:
        root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        path = root / f"{stamp}_{kind}.wav"
        _write_wav(pcm, sample_rate, path)
        for stale in sorted(root.glob("*.wav"))[:-_HARVEST_KEEP]:
            stale.unlink(missing_ok=True)
    except OSError as exc:
        log.debug("false-wake harvest failed: %s", exc)
        return None
    return path


def _save_clip(pcm: np.typing.ArrayLike, sample_rate: int, text: str,
               root: Path) -> None:
    """Keep the just-transcribed utterance audio in the rolling clips dir and
    point ``last.json`` at it. The remember tools claim the clip by matching
    ``text`` against the utterance they're storing (a file handshake instead of
    widening the closed asr.final schema). Best-effort — the voice path never
    breaks over a failed clip save."""
    try:
        root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S%f")
        path = root / f"clip-{stamp}.wav"
        _write_wav(pcm, sample_rate, path)
        marker = {
            "path": str(path),
            "text": text,
            "duration_s": round(len(np.asarray(pcm)) / sample_rate, 1),
            "ts": datetime.now(UTC).isoformat(),
        }
        tmp = root / "last.json.tmp"
        tmp.write_text(json.dumps(marker, ensure_ascii=False), encoding="utf-8")
        tmp.replace(root / "last.json")
        for stale in sorted(root.glob("clip-*.wav"))[:-_CLIPS_KEEP]:
            stale.unlink(missing_ok=True)
    except OSError as exc:
        log.debug("clip save failed: %s", exc)


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


async def _capture_memo(
    pub: Publisher,
    ring: RingReader,
    transcriber: object,
    *,
    speech_rms: float,
    max_s: float = 60.0,
    silence_s: float = 1.5,
    lead_in_s: float = 8.0,
) -> None:
    """Dictation capture for a voice memo: record from NOW until ``silence_s``
    of quiet after speech was heard (hard cap ``max_s``; abort if nothing is
    said within ``lead_in_s``), transcribe the SAME pcm, persist the wav next
    to memory.json and publish ``system.event kind=memo_recorded``.

    The ring holds only a few seconds, so audio is accumulated incrementally —
    reading the whole minute at the end would find it overwritten."""
    sr = ring.sample_rate
    tick_s = 0.2
    chunks: list[np.ndarray] = []
    last = ring.write_pos
    heard = False
    quiet_s = 0.0
    total_s = 0.0
    while True:
        await asyncio.sleep(tick_s)
        end = ring.write_pos
        if end > last:
            chunk = np.asarray(ring.read_range(last, end), dtype=np.int16)
            last = end
            chunks.append(chunk)
            total_s += len(chunk) / sr
            arr = chunk.astype(np.float64)
            rms = float(np.sqrt((arr**2).mean())) if len(arr) else 0.0
            if rms >= speech_rms:
                heard = True
                quiet_s = 0.0
            elif heard:
                quiet_s += len(chunk) / sr
        if heard and quiet_s >= silence_s:
            break
        if total_s >= max_s:
            break
        if not heard and total_s >= lead_in_s:
            break
    if not heard or not chunks:
        log.info("memo capture: no speech within %.0fs — aborting", lead_in_s)
        await pub.publish(Envelope(
            topic="error", source="blazend-asr",
            data={"code": "asr.no_speech", "message": "memo capture empty"}))
        return
    pcm = np.concatenate(chunks)
    duration_s = len(pcm) / sr
    result = await asyncio.to_thread(transcriber.transcribe, pcm, sr)  # type: ignore[attr-defined]
    try:
        memos = data_dir() / "voice_notes"
        memos.mkdir(parents=True, exist_ok=True)
        path = memos / f"memo-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.wav"
        _write_wav(pcm, sr, path)
    except OSError as exc:
        log.warning("memo wav save failed: %s", exc)
        await pub.publish(Envelope(
            topic="error", source="blazend-asr",
            data={"code": "asr.memo_save_failed", "message": str(exc)}))
        return
    await pub.publish(Envelope(
        topic="system.event", source="blazend-asr",
        data={
            "kind": "memo_recorded",
            "audio_path": str(path),
            "transcript": result.text,
            "language": result.language or "pl",
            "duration_s": round(duration_s, 1),
            "confidence": round(result.confidence, 3),
        }))
    log.info("memo recorded %.1fs → %s (%r)", duration_s, path.name, result.text[:60])


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
    memo_cfg = _cfg.get("memo_capture", {}) or {}
    harvest = bool(load("wake-word").get("harvest_false_wakes", True))
    speaker_busy = rt / "speaker-busy"
    memo_marker = rt / "memo-capture"
    memo_marker.unlink(missing_ok=True)  # stale marker from a crash mid-memo
    clips_dir = data_dir() / "clips"
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
    #
    # The wake wait is raced against the orchestrator's `memo-capture` marker:
    # touching it switches this loop into one dictation capture (a voice memo,
    # up to memo_capture.max_s, silence-terminated) and back.
    while True:
        env: Envelope | None = None
        got_frame = True
        try:
            env = await asyncio.wait_for(sub.next(), timeout=0.2)
        except TimeoutError:
            got_frame = False  # just a poll tick
        if memo_marker.exists():
            memo_marker.unlink(missing_ok=True)
            await _capture_memo(
                pub, ring, transcriber,
                # The user dictates deliberately (close mic, ducked speaker) —
                # the idle command floor is the right speech threshold.
                speech_rms=min_rms,
                max_s=float(memo_cfg.get("max_s", 60.0)),
                silence_s=float(memo_cfg.get("silence_s", 1.5)),
                lead_in_s=float(memo_cfg.get("lead_in_s", 8.0)),
            )
            continue
        if not got_frame:
            continue
        if env is None:  # publisher EOF — exit like the old async-for did
            return
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
            if harvest and (saved := _save_false_wake(pcm, ring.sample_rate, "empty")):
                log.info("harvested false-wake clip %s", saved.name)
            # Wake fired but the window came back empty — no command spoken (or too
            # quiet/far to clear the floor). Signal it so the orchestrator can prompt
            # "Słucham?" rather than leave a blind user in silence. Distinct from
            # asr.no_text (there WAS speech-level audio, but no words were recognised).
            await pub.publish(
                Envelope(
                    topic="error",
                    source="blazend-asr",
                    data={"code": "asr.no_speech", "message": "capture window empty"},
                )
            )
            continue
        result = await asyncio.to_thread(transcriber.transcribe, pcm, ring.sample_rate)
        if result.text:
            # Keep the utterance audio briefly claimable: "zapamiętaj, że …"
            # stores the memory as sound + text by picking this clip up.
            _save_clip(pcm, ring.sample_rate, result.text, clips_dir)
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
            if harvest and (saved := _save_false_wake(pcm, ring.sample_rate, "notext")):
                log.info("harvested false-wake clip %s", saved.name)
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
