#!/usr/bin/env python3
"""Jessica agent diagnostics — run the brain end-to-end with verbose, per-stage logs.

One harness to watch every aspect of an interaction: wake triggers + IPC events,
audio capture, voice recognition (ASR), intent routing, semantic note retrieval
(RAG) with cosine scores, the LLM call (incl. the *exact* system prompt sent,
chosen engine, latency), voice synthesis (TTS), and any errors — each tagged,
timestamped, and timed.

Modes (no mic/Rust units needed except --live):
    agent-diag.sh "jaka jest pogoda"      # one typed command through the full brain
    agent-diag.sh --repl                  # interactive: type commands, watch the logs
    agent-diag.sh --wav clip.wav          # feed real audio → ASR → brain
    agent-diag.sh --live                  # attach to the running mic ring + wake socket

Flags: --no-speak (log TTS instead of playing it), --data-dir DIR (memory store),
--no-llm (skip the local model load for a fast routing-only check).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
import wave
from pathlib import Path

import numpy as np
import numpy.typing as npt

from blazend.config import load
from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.engine import (
    Assistant,
    Reply,
    detect_lang,
)
from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.gemini import GeminiClient
from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.openai import OpenAiClient
from blazend.domains.context.adapters.rpi5.embeddings import Embedder
from blazend.domains.context.adapters.rpi5.memory import MemoryStore
from blazend.domains.local_ai.adapters.rpi5.localllm import LocalLlm
from blazend.domains.voice_input.adapters.rpi5.asr.engine import Transcriber, Transcript
from blazend.domains.voice_input.adapters.rpi5.voice.runner import PiperSink

# ---- logging -------------------------------------------------------------
_COLORS = {
    "WAKE": "\033[1;33m", "EVENT": "\033[33m", "AUDIO": "\033[36m",
    "ASR": "\033[34m", "INTENT": "\033[35m", "RAG": "\033[32m",
    "LLM": "\033[1;35m", "TTS": "\033[36m", "ERROR": "\033[1;31m",
    "CFG": "\033[2m", "TURN": "\033[1;37m", "LED": "\033[1;32m", "RADIO": "\033[1;36m",
}
_RESET = "\033[0m"
_USE_COLOR = sys.stdout.isatty()


class _StageFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts = time.strftime("%H:%M:%S", time.localtime(record.created))
        ts = f"{ts}.{int(record.msecs):03d}"
        stage = record.name.rsplit(".", 1)[-1].upper()[:6]
        msg = record.getMessage()
        if record.levelno >= logging.ERROR:
            stage = "ERROR"
        if _USE_COLOR:
            col = _COLORS.get(stage, "")
            return f"\033[2m{ts}\033[0m {col}{stage:<6}{_RESET} {msg}"
        return f"{ts} {stage:<6} {record.levelname:<5} {msg}"


def _setup_logging() -> None:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(_StageFormatter())
    root = logging.getLogger()
    root.handlers[:] = [h]
    root.setLevel(logging.DEBUG)
    # Quiet the noisy ORT GPU-probe warning on the Pi.
    logging.getLogger("onnxruntime").setLevel(logging.ERROR)


def _log(stage: str, msg: str, *args: object, level: int = logging.INFO) -> None:
    logging.getLogger(f"diag.{stage}").log(level, msg, *args)


# ---- instrumented seams --------------------------------------------------
class _LoggingLlm:
    """Wraps any chat client (LocalLlm/OpenAi/Gemini) to log the full exchange."""

    def __init__(self, inner: object, label: str) -> None:
        self._inner = inner
        self._label = label

    def __getattr__(self, name: str) -> object:  # grounded(), etc.
        return getattr(self._inner, name)

    @property
    def available(self) -> bool:
        return bool(getattr(self._inner, "available", False))

    def chat(self, user: str, *, system: str | None = None) -> str:
        _log("LLM", "engine=%s  user=%r", self._label, user)
        if system:
            _log("LLM", "system prompt sent (%d chars):\n  %s",
                 len(system), system.replace("\n", "\n  "), level=logging.DEBUG)
        t = time.perf_counter()
        try:
            answer = self._inner.chat(user, system=system)  # type: ignore[attr-defined]
        except Exception as e:
            _log("LLM", "engine=%s FAILED: %s", self._label, e, level=logging.ERROR)
            raise
        dt = (time.perf_counter() - t) * 1000
        _log("LLM", "engine=%s answered in %.0f ms: %r", self._label, dt, answer)
        return answer


class _LoggingMemory:
    """Proxies a MemoryStore, logging semantic retrieval with per-note cosines."""

    def __init__(self, inner: MemoryStore) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    def search_notes_semantic(
        self, query_vec: object, *, limit: int = 4, min_score: float = 0.0,
        rel_margin: float = 0.0,
    ) -> list[object]:
        emb = self._inner._load_embeddings()  # noqa: SLF001  (diagnostic introspection)
        vectors = emb.get("vectors", {})
        by_id = {n.id: n for n in self._inner.notes()}
        q = np.asarray(query_vec, dtype=np.float32)
        q = q / (float(np.linalg.norm(q)) or 1.0)
        scored = []
        for nid, vec in vectors.items():
            note = by_id.get(nid)
            if note is None:
                continue
            v = np.asarray(vec, dtype=np.float32)
            scored.append((float(q @ (v / (float(np.linalg.norm(v)) or 1.0))), note))
        scored.sort(key=lambda t: t[0], reverse=True)
        for s, n in scored[:8]:
            _log("RAG", "  cosine=%.3f  %s", s, n.title or n.text[:48], level=logging.DEBUG)
        result = self._inner.search_notes_semantic(
            query_vec, limit=limit, min_score=min_score, rel_margin=rel_margin  # type: ignore[arg-type]
        )
        _log("RAG", "floor=%.2f margin=%.2f → injected %d/%d notes: %s",
             min_score, rel_margin, len(result), len(scored),
             [n.title or n.text[:24] for n in result])
        return result


class _DiagSink:
    """Wraps PiperSink: logs the TTS stage and survives synth failures."""

    def __init__(self, inner: PiperSink, voices: dict[str, str], *, speak: bool) -> None:
        self._inner = inner
        self._voices = voices
        self._speak = speak

    def _voice_for(self, language: str) -> str:
        return self._voices.get(language, self._voices.get("pl", "?"))

    def speak(self, text: str, language: str) -> None:
        voice = self._voice_for(language)
        exists = Path(voice).exists()
        _log("TTS", "synth lang=%s voice=%s%s text=%r",
             language, Path(voice).name, "" if exists else " [MISSING!]", text)
        if not self._speak:
            return
        if not exists:
            _log("TTS", "voice model missing — cannot synthesise: %s", voice,
                 level=logging.ERROR)
            return
        t = time.perf_counter()
        try:
            self._inner.speak(text, language)
        except Exception as e:
            _log("TTS", "synth/playback FAILED: %s", e, level=logging.ERROR)
            return
        _log("TTS", "spoken in %.0f ms", (time.perf_counter() - t) * 1000)

    def acknowledge(self) -> None:
        _log("TTS", "acknowledge cue")
        if self._speak:
            try:
                self._inner.acknowledge()
            except Exception as e:
                _log("TTS", "ack FAILED: %s", e, level=logging.ERROR)

    def play_wav(self, path: str | Path) -> None:
        _log("TTS", "play wav %s", path)
        if self._speak:
            try:
                self._inner.play_wav(path)
            except Exception as e:
                _log("TTS", "play FAILED: %s", e, level=logging.ERROR)


# ---- build the instrumented brain ----------------------------------------
def _build(data_dir: Path, *, use_llm: bool, speak: bool) -> tuple[Assistant, _DiagSink]:
    from blazend.domains.voice_input.adapters.rpi5.voice.__main__ import (
        _voices,  # reuse the real voice resolver
    )

    real_mem = MemoryStore(data_dir / "memory.json")
    embedder = Embedder()
    llm = LocalLlm() if use_llm else None
    brain = Assistant(
        memory=_LoggingMemory(real_mem),  # type: ignore[arg-type]
        gemini=_LoggingClient(GeminiClient(), "gemini"),  # type: ignore[arg-type]
        llm=_LoggingClient(llm, "local") if llm is not None else None,  # type: ignore[arg-type]
        openai=_LoggingClient(OpenAiClient(), "openai"),  # type: ignore[arg-type]
        embedder=embedder,
        always_awake=True,
    )
    voices = _voices()
    piper = os.environ.get("BLAZEN_PIPER", "piper")
    out = os.environ.get("PTT_OUT", "plughw:CARD=wm8960soundcard,DEV=0")
    sink = _DiagSink(PiperSink(piper=piper, voices=voices, out_device=out), voices, speak=speak)
    return brain, sink


# _LoggingClient is an alias kept readable above; defined here for both LLM + cloud.
_LoggingClient = _LoggingLlm


def _preflight(data_dir: Path, *, use_llm: bool, speak: bool) -> None:
    _log("CFG", "=== preflight ===")
    asr = load("asr")
    _log("CFG", "ASR model=%s lang=%s", asr.get("active", "?"), asr.get("language", "auto"))
    llm_cfg = load("llm")
    from blazend.domains.local_ai.adapters.rpi5.localllm import resolve_model_path
    mp = resolve_model_path(llm_cfg)
    _log("CFG", "LLM model=%s path=%s exists=%s use_llm=%s",
         llm_cfg.get("active_model"), mp, mp.exists() if mp else False, use_llm)
    emb = Embedder()
    _log("CFG", "Embedder available=%s model=%s", emb.available, emb.name)
    from blazend.domains.voice_input.adapters.rpi5.voice.__main__ import _voices
    voices = _voices()
    for lang, path in voices.items():
        _log("CFG", "TTS voice[%s]=%s exists=%s", lang, Path(path).name, Path(path).exists())
    _log("CFG", "TTS speak=%s out=%s piper=%s", speak,
         os.environ.get("PTT_OUT", "plughw:CARD=wm8960soundcard,DEV=0"),
         os.environ.get("BLAZEN_PIPER", "piper"))
    _log("CFG", "cloud keys: OPENAI=%s GEMINI=%s",
         bool(os.environ.get("OPENAI_API_KEY")), bool(os.environ.get("GEMINI_API_KEY")))
    _log("CFG", "memory dir=%s", data_dir)


# ---- ASR with per-language logging ---------------------------------------
def _transcribe(transcriber: Transcriber, pcm: npt.NDArray[np.int16]) -> Transcript:
    result: Transcript | None = None
    for lang in ("pl", "en"):
        transcriber.language_mode = lang
        t = time.perf_counter()
        result = transcriber.transcribe(pcm, 16_000)
        dt = (time.perf_counter() - t) * 1000
        _log("ASR", "try lang=%s → %.0f ms conf=%.2f text=%r",
             lang, dt, result.confidence, result.text)
        if result.text.strip():
            break
    assert result is not None
    _log("ASR", "chosen [%s] conf=%.2f: %r", result.language, result.confidence, result.text)
    return result


# ---- one interaction -----------------------------------------------------
def _handle_text(brain: Assistant, sink: _DiagSink, text: str) -> Reply:
    t0 = time.perf_counter()
    _log("TURN", "──────── command: %r ────────", text)
    _log("INTENT", "detected language=%s", detect_lang(text))

    # Stream the freeform-LLM path sentence-by-sentence so the logs show the
    # perceived-latency win: trigger → first token → first sentence → first audio.
    marks: dict[str, float] = {}

    def on_token() -> None:
        if "ft" not in marks:
            marks["ft"] = time.perf_counter()
            _log("LLM", "first token @ %.0f ms", (marks["ft"] - t0) * 1000)

    def on_sentence(sentence: str, language: str) -> None:
        ms = (time.perf_counter() - t0) * 1000
        first = "fs" not in marks
        marks.setdefault("fs", time.perf_counter())
        _log("TTS", "%s sentence @ %.0f ms [%s]: %r",
             "first" if first else "next", ms, language, sentence)
        sink.speak(sentence, language)

    t = time.perf_counter()
    reply = brain.route(text, now=_now(), on_sentence=on_sentence, on_token=on_token)
    _log("INTENT", "action=%s lang=%s streamed=%s (route %.0f ms)",
         reply.action, reply.language, reply.data.get("streamed", False),
         (time.perf_counter() - t) * 1000)
    if reply.data:
        _log("INTENT", "reply data=%s", reply.data)
    _log("TURN", "reply: %r", reply.text)
    # Non-streamed paths (commands, cloud, errors) still speak the whole reply.
    if not reply.data.get("streamed") and reply.text.strip():
        sink.speak(reply.text, reply.language)
    _log("TURN", "── done in %.0f ms ──", (time.perf_counter() - t0) * 1000)
    return reply


def _now():  # noqa: ANN202  (datetime import kept local to the call site)
    from datetime import datetime
    return datetime.now()


def _read_wav(path: Path) -> npt.NDArray[np.int16]:
    with wave.open(str(path), "rb") as w:
        sr, ch, n = w.getframerate(), w.getnchannels(), w.getnframes()
        raw = w.readframes(n)
    pcm = np.frombuffer(raw, dtype="<i2")
    if ch > 1:
        pcm = pcm.reshape(-1, ch).mean(axis=1).astype(np.int16)
    rms = float(np.sqrt(np.mean(pcm.astype(np.float32) ** 2))) if pcm.size else 0.0
    peak = int(np.abs(pcm).max()) if pcm.size else 0
    clip = float(np.mean(np.abs(pcm) >= 32_000) * 100) if pcm.size else 0.0
    _log("AUDIO", "wav %s: %d samples @%dHz (%.2fs) rms=%.0f peak=%d clip=%.1f%%",
         path.name, pcm.size, sr, pcm.size / max(sr, 1), rms, peak, clip)
    if sr != 16_000:
        _log("AUDIO", "note: ASR expects 16kHz; file is %dHz", sr, level=logging.ERROR)
    return pcm.astype(np.int16)


# ---- live mode -----------------------------------------------------------
async def _run_live(
    brain: Assistant,
    sink: _DiagSink,
    capture_s: float,
    led: object,
    radio: object,
) -> None:
    from blazend.domains.systems.adapters.rpi5.led import BLUE, GREEN, MAGENTA, OFF, RED
    from blazend.domains.voice_input.adapters.rpi5.audio import RingReader
    from blazend.domains.voice_input.adapters.rpi5.voice.runner import RingCapturer
    from blazend.ipc import Subscriber, runtime_dir

    def set_led(color: str, meaning: str) -> None:
        led.set(color)  # type: ignore[attr-defined]
        _log("LED", "%s — %s", color, meaning)

    rt = runtime_dir()
    _log("EVENT", "attaching to ring=%s wake.sock=%s", rt / "audio-ring.shm", rt / "wake.sock")
    ring = RingReader(rt / "audio-ring.shm")
    capturer = RingCapturer(ring)
    transcriber = Transcriber()
    sub = Subscriber(rt / "wake.sock")
    await sub.connect()
    set_led(GREEN, "listening for the wake word")
    _log("EVENT", "listening for IPC events — say 'Hej Jessico' (Ctrl-C to stop)")
    try:
        async for env in sub:
            _log("EVENT", "topic=%s source=%s data=%s", env.topic, env.source, env.data)
            if env.topic != "wake.detected":
                continue
            score = env.data.get("score")
            # Free the speaker from any playing radio so Jessica can be heard.
            radio.stop()  # type: ignore[attr-defined]
            set_led(BLUE, "wake detected — capturing the command")
            _log("WAKE", "TRIGGER score=%s → acknowledging + capturing %.1fs", score, capture_s)
            sink.acknowledge()
            pcm = await capturer.capture(capture_s)
            _log("AUDIO", "captured %d samples (%.2fs) from ring", pcm.size, pcm.size / 16_000)
            set_led(MAGENTA, "processing (ASR / route / TTS)")
            transcript = _transcribe(transcriber, pcm)
            if not transcript.text.strip():
                _log("ASR", "empty transcript — ignoring", level=logging.INFO)
                set_led(GREEN, "back to listening")
                continue
            reply = _handle_text(brain, sink, transcript.text)
            # Act on radio intents (the diag's own loop owns playback here).
            if reply.action == "radio_play" and reply.data.get("url"):
                _log("RADIO", "play %s → %s", reply.data.get("name"), reply.data.get("url"))
                radio.play(reply.data["url"], reply.data.get("name", ""))  # type: ignore[attr-defined]
            elif reply.action == "radio_stop":
                _log("RADIO", "stop")
                radio.stop()  # type: ignore[attr-defined]
            if reply.action == "error":
                set_led(RED, "error")
            set_led(GREEN, "back to listening")
    finally:
        led.set(OFF)  # type: ignore[attr-defined]
        _log("LED", "off — stopped")


# ---- entrypoint ----------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="Jessica agent diagnostics")
    ap.add_argument("command", nargs="*", help="a typed command to run through the brain")
    ap.add_argument("--repl", action="store_true", help="interactive prompt loop")
    ap.add_argument("--wav", type=Path, help="feed a WAV file through ASR → brain")
    ap.add_argument("--live", action="store_true", help="attach to the live mic ring + wake socket")
    ap.add_argument("--no-speak", action="store_true", help="log TTS instead of playing audio")
    ap.add_argument("--no-llm", action="store_true", help="skip local LLM load (routing only)")
    ap.add_argument("--data-dir", type=Path, help="memory store dir (default $BLAZEN_DATA_DIR)")
    args = ap.parse_args()

    _setup_logging()
    data_dir = args.data_dir or Path(os.environ.get("BLAZEN_DATA_DIR", "/tmp/blazen-diag"))
    data_dir.mkdir(parents=True, exist_ok=True)
    speak = not args.no_speak
    use_llm = not args.no_llm

    _preflight(data_dir, use_llm=use_llm, speak=speak)
    brain, sink = _build(data_dir, use_llm=use_llm, speak=speak)

    if args.live:
        from blazend.domains.systems.adapters.rpi5.led_hw import open_status_led
        from blazend.domains.voice_input.adapters.rpi5.voice.runner import StreamPlayer
        capture_s = float(load("wake-word").get("capture_window_s", 4.5))
        out = os.environ.get("PTT_OUT", "plughw:CARD=wm8960soundcard,DEV=0")
        led = open_status_led()
        _log("LED", "status LED: %s", type(led).__name__)
        radio = StreamPlayer(device=out, player=os.environ.get("BLAZEN_PLAYER", "blazend-player"))
        try:
            asyncio.run(_run_live(brain, sink, capture_s, led, radio))
        except KeyboardInterrupt:
            _log("EVENT", "stopped")
        finally:
            radio.stop()
            led.close()
        return 0

    if args.wav:
        transcriber = Transcriber()
        pcm = _read_wav(args.wav)
        transcript = _transcribe(transcriber, pcm)
        if transcript.text.strip():
            _handle_text(brain, sink, transcript.text)
        return 0

    if args.command:
        _handle_text(brain, sink, " ".join(args.command))
        return 0

    # default: REPL
    _log("TURN", "type a command (empty line or Ctrl-D to quit)")
    while True:
        try:
            line = input("\033[1;32myou> \033[0m" if _USE_COLOR else "you> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            break
        _handle_text(brain, sink, line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
