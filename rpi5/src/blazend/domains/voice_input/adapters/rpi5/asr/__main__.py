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
import os
import re
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
# Follows BLAZEN_DATA_DIR's parent on desktop installs (2026-08-23); the Pi
# default is unchanged.
_HARVEST_DIR = (
    Path(os.environ["BLAZEN_DATA_DIR"]).parent / "wake-negatives"
    if os.environ.get("BLAZEN_DATA_DIR")
    else Path("/var/lib/blazen/wake-negatives")
)
_HARVEST_KEEP = 200  # newest clips kept; ~160 KB each → ~32 MB cap

# Rolling utterance clips: every SUCCESSFULLY transcribed post-wake window is
# kept briefly (audio + its text in last.json) so a memory command spoken in the
# same breath — "zapamiętaj, że …" — can claim its own recording and store the
# memory as sound + text. Unclaimed clips just age out of the ring; nothing
# leaves the device.
_CLIPS_KEEP = 50

# Wake-like tokens (folded), shared shape with scripts/screen-wake-harvest.py.
# A wake-triggered capture window starts 2 s BEFORE the detection, so a REAL
# wake's transcript contains the wake word (possibly mangled — hence fuzzy
# spellings). A window that transcribes to fluent speech WITHOUT any such
# token is ambient/radio talk that fooled the model — the most valuable kind
# of wake negative, which the empty/notext harvest branches never see
# (radio speech transcribes fine; observed 2026-08-29: a 20-minute storm
# left ONE harvested clip).
_WAKE_LIKE = re.compile(r"(dzesik|dziesik|jessic|jessik|jesik|dzesic|czesik|gesik)")


# Stock whisper hallucinations on silence/noise — YouTube-outro phrases burned
# into the model (live 2026-08-31 18:15-18:20: "Dziękuje za oglądanie." over
# and over kept a wake-less conversation loop alive for 12+ minutes). Folded
# substring match; a real command never contains these.
_SILENCE_PHRASES = (
    "dziekuje za ogladanie", "dziekuje za obejrzenie", "dziekuje za uwage",
    "napisy stworzone przez", "amara.org", "zapraszam na kolejny",
    "do zobaczenia w kolejnym", "zasubskrybuj", "thanks for watching",
)


def _looks_hallucinated(text: str) -> bool:
    """Whisper repetition loop on noise/silence — seeded by the hotwords list
    (live 2026-08-31 08:44: a follow-up on room noise transcribed to
    "…Kraków" repeated nineteen times). One token dominating a longish
    transcript is a hallucination, not speech. Also matches the model's
    stock silence phrases (_SILENCE_PHRASES)."""
    from audiobook_catalog.text_norm import _fold

    folded = _fold(text)
    if any(p in folded for p in _SILENCE_PHRASES):
        return True
    toks = text.lower().split()
    # ONE word repeated with nothing else is a loop at any length ≥3:
    # "Kraków Kraków Kraków" (conf 0.47, silence, 2026-09-08) slipped the
    # 6-token minimum below and STARTED Radio Kraków via the short-ask gate.
    if len(toks) >= 3 and len(set(toks)) == 1:
        return True
    if len(toks) < 6:
        return False
    counts = sorted((toks.count(t) for t in set(toks)), reverse=True)
    top = counts[0]
    # Two-token alternating loops ("Dżesika, Dwójka," ×27, live 2026-08-30)
    # split the count 50/50, so the single-token share alone misses them.
    top2 = top + (counts[1] if len(counts) > 1 else 0)
    return top >= 5 and (top / len(toks) > 0.5 or top2 / len(toks) > 0.8)


def _wake_like(text: str) -> bool:
    """True if the transcript plausibly contains the wake word."""
    from audiobook_catalog.text_norm import _fold

    return bool(_WAKE_LIKE.search(_fold(text)))


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


async def _record_until_silence(
    ring: RingReader,
    *,
    speech_rms: float,
    max_s: float,
    silence_s: float,
    lead_in_s: float,
) -> tuple[bool, list[np.ndarray]]:
    """Record from NOW until ``silence_s`` of quiet after speech (hard cap
    ``max_s``; give up after ``lead_in_s`` with no speech). Accumulates
    incrementally — the ring only holds a few seconds. Returns
    ``(heard_speech, chunks)``. Shared by memo dictation and the
    conversation follow-up capture (2026-08-30)."""
    sr = ring.sample_rate
    tick_s = 0.2
    chunks: list[np.ndarray] = []
    last = ring.write_pos
    heard = False
    quiet_s = 0.0
    total_s = 0.0
    wall_s = 0.0
    while True:
        await asyncio.sleep(tick_s)
        # Wall-clock bound: total_s advances only with fresh ring frames, so a
        # stalled ring (audio-in died mid-capture) would otherwise spin forever.
        wall_s += tick_s
        if wall_s >= max_s + silence_s + 2.0:
            break
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
    return heard, chunks


async def _capture_followup(
    pub: Publisher,
    ring: RingReader,
    transcriber: object,
    lexicon: object | None,
    lex_ratio: float,
    clips_dir: Path,
    *,
    speech_rms: float,
    max_s: float = 12.0,
    silence_s: float = 1.2,
    lead_in_s: float = 5.0,
    min_conf: float = 0.6,
    speaker_id: object | None = None,
    speaker_thr: float = 0.45,
) -> None:
    """Conversation-mode follow-up (2026-08-30): after Jessica speaks an LLM
    reply the orchestrator opens this window so the user can answer WITHOUT
    the wake word. A caught utterance flows down the normal command path
    (published as ``asr.final``, with the lexicon repair); silence ends the
    exchange with ``asr.followup_empty`` — the orchestrator just closes the
    conversation, no nagging."""
    heard, chunks = await _record_until_silence(
        ring, speech_rms=speech_rms, max_s=max_s, silence_s=silence_s,
        lead_in_s=lead_in_s)
    if not heard or not chunks:
        await pub.publish(Envelope(
            topic="error", source="blazend-asr",
            data={"code": "asr.followup_empty", "message": "follow-up window empty"}))
        return
    pcm = np.concatenate(chunks)
    result = await asyncio.to_thread(
        transcriber.transcribe, pcm, ring.sample_rate)  # type: ignore[attr-defined]
    text = result.text
    if text and lexicon is not None:
        text, hits = lexicon.correct(text, fuzzy_ratio=lex_ratio)  # type: ignore[attr-defined]
        if hits:
            log.info("asr corrected (follow-up): %s", "; ".join(hits))
    if not text or _looks_hallucinated(text):
        await pub.publish(Envelope(
            topic="error", source="blazend-asr",
            data={"code": "asr.followup_empty", "message": "no words in follow-up"}))
        return
    # A follow-up continues a conversation WITHOUT a wake word, so it must
    # clear a confidence bar a wake-gated command never needed: room babble
    # that whisper "understood" at conf 0.42-0.55 kept a wake-less loop
    # talking for 12+ minutes (2026-08-31 18:07-18:20, no human in the
    # exchange). Below the bar the conversation just closes; a real user
    # re-opens it with the wake word.
    if result.confidence < min_conf:
        log.info("follow-up below min_conf %.2f: %r (conf=%.2f) — closing",
                 min_conf, text[:60], result.confidence)
        await pub.publish(Envelope(
            topic="error", source="blazend-asr",
            data={"code": "asr.followup_empty",
                  "message": "follow-up below confidence floor"}))
        return
    _save_clip(pcm, ring.sample_rate, text, clips_dir)
    sp = _speaker_fields(speaker_id, pcm, speaker_thr)
    await pub.publish(Envelope(
        topic="asr.final", source="blazend-asr",
        data={"language": result.language, "text": text,
              "confidence": round(result.confidence, 3), **sp}))
    log.info("asr.final (follow-up) %s %r (conf=%.2f%s)",
             result.language, text, result.confidence,
             f" sim={sp['speaker_sim']:.2f}" if sp else "")


def _speaker_fields(speaker_id: object | None, pcm: np.typing.ArrayLike,
                    threshold: float) -> dict[str, float | bool]:
    """speaker_sim/speaker_owner for an utterance — {} when identification is
    impossible (no model/profile/too short): downstream trusts the voice."""
    if speaker_id is None:
        return {}
    try:
        sim = speaker_id.similarity(pcm)  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001 — never break the voice path
        log.debug("speaker id failed: %s", exc)
        return {}
    if sim is None:
        return {}
    return {"speaker_sim": round(float(sim), 3),
            "speaker_owner": bool(sim >= threshold)}


# Wake-less STOP phrases (folded): single tokens matched exactly, multi-word
# matched as a phrase. Deliberately unambiguous — anything else spoken over a
# stream is DISCARDED by _check_stop, never routed.
_STOP_TOKENS = ("stop", "przestan", "dosc", "cisza", "koniec", "pauza", "wylacz",
                # EN asset parity (docs/13): retained though runtime is PL-only.
                "quiet", "silence", "enough")
_STOP_PHRASES = ("zamknij sie", "shut up", "be quiet")


async def _check_stop(
    pub: Publisher,
    ring: RingReader,
    transcriber: object,
    *,
    span_s: float = 4.0,
    min_conf: float = 0.5,
) -> None:
    """Wake-less stop check (2026-09-08): while Jessica plays a stream or speaks
    a long reply the wake word is unreliable — the Jabra DSP attenuates the mic
    and "Dżesika, stop!" scored below even the near-miss band, leaving the user
    UNABLE to stop the radio by voice. The orchestrator arms this on any short
    VAD utterance during her own activity; the last few seconds are transcribed
    and ONLY a short, confident, unambiguous stop phrase acts (published as a
    canonical "stop" command). Everything else — room talk, radio leak-through,
    her own voice — is silently discarded: no chat fallback, no reply."""
    import re

    from audiobook_catalog.text_norm import _fold

    end = ring.write_pos
    pcm = np.asarray(
        ring.read_range(max(0, end - int(span_s * ring.sample_rate)), end),
        dtype=np.int16,
    )
    if len(pcm) < ring.sample_rate // 2:
        return
    result = await asyncio.to_thread(
        transcriber.transcribe, pcm, ring.sample_rate)  # type: ignore[attr-defined]
    text = (result.text or "").strip()
    if not text or result.confidence < min_conf or _looks_hallucinated(text):
        return
    toks = re.sub(r"[^\w\s]", " ", _fold(text)).split()
    if not toks or len(toks) > 6:  # a stop order is short; babble is not
        return
    joined = " ".join(toks)
    if not (any(t in _STOP_TOKENS for t in toks)
            or any(p in joined for p in _STOP_PHRASES)):
        log.info("stop check: no stop phrase in %r — ignored", text[:60])
        return
    log.info("stop check HIT %r (conf=%.2f) — routing canonical stop",
             text[:60], result.confidence)
    await pub.publish(Envelope(
        topic="asr.final", source="blazend-asr",
        data={"language": result.language, "text": "stop",
              "confidence": round(result.confidence, 3)}))


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
    heard, chunks = await _record_until_silence(
        ring, speech_rms=speech_rms, max_s=max_s, silence_s=silence_s,
        lead_in_s=lead_in_s)
    if not heard or not chunks:
        log.info("memo capture: no speech within %.0fs — aborting", lead_in_s)
        # Distinct code: the orchestrator answers with a memo-specific retry
        # prompt, not the generic empty-capture "Słucham?".
        await pub.publish(Envelope(
            topic="error", source="blazend-asr",
            data={"code": "asr.memo_empty", "message": "memo capture empty"}))
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
    from blazend.domains.voice_input.adapters.rpi5.asr.lexicon import get_lexicon

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
    fu_cfg = _cfg.get("followup_capture", {}) or {}
    # Post-ASR lexicon repair (asr.yaml `lexicon:`): fuzzy-match transcript
    # spans against the domain vocabulary (commands, stations, titles) so one
    # whisper slip ("Top!" for "stop!") doesn't kill the command. Commands
    # only — memo dictation stays verbatim.
    lex_correct = bool(_cfg.get("lexicon.correct", True))
    lex_ratio = float(_cfg.get("lexicon.fuzzy_ratio", 0.78))
    min_final_conf = float(_cfg.get("min_final_conf", 0.5))
    lexicon = get_lexicon() if lex_correct else None
    harvest = bool(load("wake-word").get("harvest_false_wakes", True))
    speaker_busy = rt / "speaker-busy"
    memo_marker = rt / "memo-capture"
    memo_marker.unlink(missing_ok=True)  # stale marker from a crash mid-memo
    followup_marker = rt / "followup-capture"
    followup_marker.unlink(missing_ok=True)
    followup_hot_marker = rt / "followup-capture-hot"
    followup_hot_marker.unlink(missing_ok=True)
    stopcheck_marker = rt / "stopcheck-capture"
    stopcheck_marker.unlink(missing_ok=True)
    clips_dir = data_dir() / "clips"
    transcriber = Transcriber()
    # Speaker-ID (2026-09-08): stamp every command/follow-up with how much it
    # sounds like the OWNER. Soft: no model/profile → no fields → downstream
    # trusts every voice. Enrollment (re)builds on the speaker-enroll marker
    # (touched by the orchestrator when voice training completes) and at
    # startup when a profile is missing but training recordings exist.
    from blazend.domains.voice_input.adapters.rpi5.asr.speaker import SpeakerId

    speaker_id = SpeakerId()
    speaker_thr = float(load("speaker-id").get("threshold", 0.45))
    enroll_marker = rt / "speaker-enroll"
    enroll_marker.unlink(missing_ok=True)
    train_dir = (Path(os.environ["BLAZEN_DATA_DIR"]).parent / "voice-training"
                 if os.environ.get("BLAZEN_DATA_DIR")
                 else Path("/var/lib/blazen/voice-training"))

    async def _enroll() -> None:
        n = await asyncio.to_thread(speaker_id.enroll_dir, train_dir)
        log.info("speaker enrollment: %d embeddings from %s", n, train_dir)

    if not speaker_id.available and train_dir.is_dir():
        asyncio.create_task(_enroll())
    log.info("asr real path: model=%s ring=%dHz fixed-window=%.1fs",
             transcriber.model, ring.sample_rate, cap_s)

    sub = await _connect(rt / "wake.sock")
    log.info("subscribed to wake.detected on wake.sock (fixed-window capture)")

    async def _idle_unload() -> None:
        # Power saving (asr.yaml unload_after_s > 0, GPU desktops): drop the
        # idle model so the card can sleep; the next wake reloads it.
        while True:
            await asyncio.sleep(30)
            try:
                if transcriber.maybe_unload():
                    log.info("asr model unloaded after %.0fs idle (power saving)",
                             transcriber.unload_after_s)
            except Exception:  # noqa: BLE001 — power saving must never kill ASR
                log.exception("idle unload failed")

    if getattr(transcriber, "unload_after_s", 0) > 0:
        asyncio.create_task(_idle_unload())

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
        if followup_marker.exists() or followup_hot_marker.exists():
            # HOT variant (2026-09-08): the LLM reply awaits an answer — the
            # user IS about to speak, so capture more sensitively: longer
            # lead-in, lower confidence floor.
            hot = followup_hot_marker.exists()
            followup_marker.unlink(missing_ok=True)
            followup_hot_marker.unlink(missing_ok=True)
            base_lead = float(fu_cfg.get("lead_in_s", 5.0))
            base_conf = float(fu_cfg.get("min_conf", 0.6))
            await _capture_followup(
                pub, ring, transcriber, lexicon, lex_ratio, clips_dir,
                # The reply just finished playing and the stream (if any) is
                # ducked — the playing floor is the right speech threshold.
                speech_rms=min_rms_playing if speaker_busy.exists() else min_rms,
                max_s=float(fu_cfg.get("max_s", 12.0)),
                silence_s=float(fu_cfg.get("silence_s", 1.2)),
                lead_in_s=base_lead + 2.0 if hot else base_lead,
                min_conf=max(0.0, base_conf - 0.1) if hot else base_conf,
                speaker_id=speaker_id, speaker_thr=speaker_thr,
            )
            continue
        if enroll_marker.exists():
            enroll_marker.unlink(missing_ok=True)
            await _enroll()
            continue
        if stopcheck_marker.exists():
            stopcheck_marker.unlink(missing_ok=True)
            await _check_stop(pub, ring, transcriber)
            continue
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
        if env is None:
            # blazend-wake restarted (its socket rebound) → EOF on the
            # subscription. RECONNECT instead of exiting: a clean exit left
            # the ASR dead after a lone wake restart — exit 0 means
            # Restart=on-failure never revives it (same bug as the brain's
            # NLU subscription, fixed 2026-08-29/30).
            await sub.close()
            sub = await _connect(rt / "wake.sock")
            log.info("asr re-subscribed to wake.detected (wake restarted)")
            continue
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
        # Track the ring until speech-then-silence instead of a fixed sleep
        # (2026-08-31): the correct flow is word → chime → command, and after
        # ~1 s of chime wait a fixed 3 s window cut longer commands mid-word —
        # "włącz trójkę" lost its tail and the truncated audio fed a whisper
        # repetition loop. A silent window still falls through after lead_in_s
        # (same worst case as the old sleep); a short command now transcribes
        # SOONER (silence ends the wait early).
        # 0.5 s settle first: detection fires near the END of the wake word,
        # but its trailing syllable would count as "speech" and the chime
        # pause as its "silence", closing the window before the command.
        await asyncio.sleep(0.5)
        cmd_heard, _ = await _record_until_silence(
            ring,
            speech_rms=min_rms_playing if speaker_busy.exists() else min_rms,
            max_s=cap_s, silence_s=1.0, lead_in_s=3.0)
        if not cmd_heard:
            # Wake word with NO command after it: transcribing the window
            # would hand whisper just the wake word's tail ("zika"), which
            # then rode the chat fallback into an LLM greeting. Say so
            # instead — the orchestrator prompts "Słucham?" on this code.
            log.info("asr: no command after wake — prompting instead of transcribing")
            await pub.publish(
                Envelope(
                    topic="error",
                    source="blazend-asr",
                    data={"code": "asr.no_speech", "message": "no command after wake"},
                )
            )
            continue
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
        text = result.text
        if text and _looks_hallucinated(text):
            # Same repetition-loop guard as the follow-up path (live 2026-08-31
            # 14:22: a real "włącz trójkę" came back as "Dżesika," ×8 and fell
            # through to the LLM chat fallback). Ask again instead of routing
            # garbage; no harvest — the window holds a REAL wake word.
            log.info("asr: hallucinated transcript dropped %r", text[:80])
            await pub.publish(
                Envelope(
                    topic="error",
                    source="blazend-asr",
                    data={"code": "asr.no_text", "message": "hallucinated transcript"},
                )
            )
            continue
        if text and result.confidence < min_final_conf:
            # Whisper this unsure is decoding noise, not a command — observed
            # garbage sat at 0.31-0.49 while real commands score 0.6+. Acting
            # on it is how "Kraków ×3" (conf 0.47) started a radio in a silent
            # room. The orchestrator's not-understood cue asks to repeat.
            log.info("asr: final below min conf %.2f dropped %r (conf=%.2f)",
                     min_final_conf, text[:60], result.confidence)
            await pub.publish(
                Envelope(
                    topic="error",
                    source="blazend-asr",
                    data={"code": "asr.no_text",
                          "message": "transcript below confidence floor"},
                )
            )
            continue
        if text and lexicon is not None:
            text, hits = lexicon.correct(text, fuzzy_ratio=lex_ratio)
            if hits:
                log.info("asr corrected: %s", "; ".join(hits))
        if text:
            # Keep the utterance audio briefly claimable: "zapamiętaj, że …"
            # stores the memory as sound + text by picking this clip up.
            _save_clip(pcm, ring.sample_rate, text, clips_dir)
            sp = _speaker_fields(speaker_id, pcm, speaker_thr)
            await pub.publish(
                Envelope(
                    topic="asr.final",
                    source="blazend-asr",
                    data={
                        "language": result.language,
                        "text": text,
                        "confidence": round(result.confidence, 3),
                        **sp,
                    },
                )
            )
            log.info(
                "asr.final %s %r (conf=%.2f%s)", result.language, text,
                result.confidence,
                f" sim={sp['speaker_sim']:.2f} owner={sp['speaker_owner']}"
                if sp else "",
            )
            # Speech-kind harvest: publishing above is UNCHANGED — this only
            # keeps the audio of a speech window with no wake-like token for
            # the next `train-wake.py --neg-dir` retrain (screened there, not
            # auto-ingested: a mis-transcribed real command stays a suspect).
            if harvest and not _wake_like(text):
                if saved := _save_false_wake(pcm, ring.sample_rate, "speech"):
                    log.info("harvested speech false-wake clip %s", saved.name)
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
