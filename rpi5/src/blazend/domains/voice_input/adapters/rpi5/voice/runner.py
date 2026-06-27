"""Hands-free Jessica (S5): "Hej Jessico" wake → fixed-window capture → whisper
→ engine → Piper, with the HAT button as a push-to-talk fallback.

This is the testable core promoted out of the `_test_projects/wake_jessica.py`
harness. The runner NEVER opens the mic: the Rust `blazend-audio-in` fills the
shared-memory ring and `blazend-wake` publishes `wake.detected`; the runner
reads the ring (:class:`blazend.domains.voice_input.adapters.rpi5.audio.RingReader`) for both wake- and
button-triggered captures, so there is no ALSA device contention. There is no
energy VAD — a fixed window after wake (`capture_window_s`) and the held window
for the button both sidestep the quiet-WM8960 fragmentation.

The hardware seams (`AudioSink`, `Capturer`, the wake source) are injectable so
the orchestration can be driven with fakes in a component test; the production
wiring lives in :func:`build_runner` and :func:`main`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import tempfile
import time
import wave
from collections.abc import AsyncIterable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import numpy as np
import numpy.typing as npt

from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.engine import Assistant, Reply
from blazend.domains.systems.adapters.rpi5.led import BLUE, GREEN, MAGENTA, OFF
from blazend.domains.systems.adapters.rpi5.led_hw import NullStatusLed, StatusLed
from blazend.domains.voice_input.adapters.rpi5.asr.engine import Transcript
from blazend.domains.voice_input.adapters.rpi5.audio import RingReader

log = logging.getLogger("blazend.domains.voice_input.adapters.rpi5.voice")

# Polish-first acknowledgement spoken the moment the wake fires, so the user
# knows to start their command. Bilingual by design (PL leads, EN co-equal).
ACK_TEXT = {"pl": "Słucham?", "en": "Yes?"}


# -- injectable seams -------------------------------------------------------
@runtime_checkable
class AudioSink(Protocol):
    """Speaker side: synthesise + play. Production impl is :class:`PiperSink`."""

    def speak(self, text: str, language: str) -> None: ...
    def acknowledge(self) -> None: ...
    def play_wav(self, path: str | Path) -> None: ...


@runtime_checkable
class Capturer(Protocol):
    """Microphone side: hand back a window of i16 PCM read from the ring."""

    @property
    def sample_rate(self) -> int: ...
    async def capture(self, seconds: float) -> npt.NDArray[np.int16]: ...


@runtime_checkable
class RadioController(Protocol):
    """Streams an internet-radio URL or local file to the speaker. Production: :class:`StreamPlayer`."""

    now_playing: str | None

    def play(self, url: str, name: str = "") -> None: ...
    def stop(self) -> None: ...


@runtime_checkable
class TranscriberLike(Protocol):
    """Subset of :class:`blazend.domains.voice_input.adapters.rpi5.asr.engine.Transcriber` the runner drives."""

    language_mode: str

    def transcribe(
        self, pcm: npt.NDArray[np.int16] | npt.NDArray[np.float32], sample_rate: int = ...
    ) -> Transcript: ...


# -- core runner ------------------------------------------------------------
@dataclass
class VoiceRunner:
    """Wires wake/capture/ASR/engine/TTS into one hands-free loop.

    Every external effect goes through an injected seam, so a test can supply a
    fake wake source, a pre-filled ring, and fake ASR/TTS to exercise the whole
    chain deterministically.
    """

    brain: Assistant
    transcriber: TranscriberLike
    capturer: Capturer
    sink: AudioSink
    capture_s: float = 4.5
    # Headless status surface: the HAT's APA102 LEDs track the pipeline state
    # (listening → capturing → processing). Defaults to a no-op so the runner
    # works unchanged with no SPI hardware (VM / dev host / tests).
    status_led: StatusLed = field(default_factory=NullStatusLed)
    radio: RadioController = field(default_factory=lambda: NullRadio())
    clock: Callable[[], datetime] = datetime.now
    clock_monotonic: Callable[[], float] = time.monotonic  # for latency marks
    _audio: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    # -- ASR -------------------------------------------------------------
    def transcribe(self, pcm: npt.NDArray[np.int16]) -> Transcript:
        """Polish-first, English fallback — whisper auto-detect is unreliable
        on the short, quiet HAT clips, so try `pl` then `en` until non-empty."""
        result: Transcript | None = None
        for lang in ("pl", "en"):
            self.transcriber.language_mode = lang
            result = self.transcriber.transcribe(pcm, 16_000)
            if result.text.strip():
                break
        assert result is not None  # the loop always runs at least once
        return result

    # -- one captured utterance -----------------------------------------
    async def handle_pcm(self, pcm: npt.NDArray[np.int16]) -> Reply | None:
        """Transcribe → route → speak. Returns the `Reply` (None if silence).

        Freeform LLM replies are spoken **sentence-by-sentence as they stream**
        (see :meth:`_streamed_route`): audio starts after the first sentence
        instead of after the whole reply — the key perceived-latency win on the
        Pi CPU. Commands and cloud replies speak as one short utterance.
        """
        self.status_led.set(MAGENTA)  # processing (ASR / route / TTS)
        try:
            transcript = await asyncio.to_thread(self.transcribe, pcm)
            log.info("heard [%s] conf=%.2f: %r",
                     transcript.language, transcript.confidence, transcript.text)
            if not transcript.text.strip():
                return None
            async with self._audio:
                reply = await self._streamed_route(transcript.text)
                log.info("jessica [%s] action=%s: %r", reply.language, reply.action, reply.text)
                # Free the speaker from any playing radio before Jessica talks
                # back (one ALSA opener at a time on the HAT). A non-radio reply
                # leaves the radio off; radio_play starts the new station below.
                await asyncio.to_thread(self.radio.stop)
                # Streamed replies already spoke each sentence; only speak here
                # for the non-streamed paths (commands, cloud, errors).
                if not reply.data.get("streamed") and reply.text.strip():
                    await self._say(reply.text, reply.language)
                if reply.action == "voice_note_play":
                    for path in reply.data.get("paths", []):
                        await asyncio.to_thread(self.sink.play_wav, path)
                elif reply.action == "radio_play":
                    url = reply.data.get("url")
                    if url:
                        await asyncio.to_thread(
                            self.radio.play, url, reply.data.get("name", ""))
            if reply.action == "voice_note_record":
                await self._record_voice_note()
            return reply
        finally:
            self.status_led.set(GREEN)  # back to listening

    async def _streamed_route(self, text: str) -> Reply:
        """Route ``text``, speaking each sentence the instant the LLM finishes it.

        The route runs in a worker thread; the engine calls our hooks from that
        thread, so speaking (a blocking Piper/aplay subprocess) happens off the
        event loop. The ``_audio`` lock is already held by the caller. Emits a
        4-stage latency log: trigger → first token → first sentence → first audio.
        """
        t0 = self.clock_monotonic()
        marks: dict[str, float] = {}

        def _ms() -> float:
            return (self.clock_monotonic() - t0) * 1000.0

        def on_token() -> None:
            marks.setdefault("first_token_ms", _ms())

        def on_sentence(sentence: str, language: str) -> None:
            marks.setdefault("first_sentence_ms", _ms())
            try:
                self.sink.speak(sentence, language)
            except Exception as e:  # a TTS failure must not kill the stream
                log.warning("TTS speak failed (%s); continuing", e)
            if "first_audio_ms" not in marks:
                marks["first_audio_ms"] = _ms()
                log.info(
                    "latency: trigger=0ms first_token=%.0fms first_sentence=%.0fms first_audio=%.0fms",
                    marks.get("first_token_ms", 0.0),
                    marks["first_sentence_ms"],
                    marks["first_audio_ms"],
                )

        return await asyncio.to_thread(
            self.brain.route, text, now=self.clock(),
            on_sentence=on_sentence, on_token=on_token,
        )

    async def _record_voice_note(self) -> None:
        """The engine asked us to record — capture the next window to disk."""
        self.status_led.set(BLUE)  # capturing the dictated note
        pcm = await self.capturer.capture(self.capture_s)
        now = self.clock()
        dest = self.brain.memory.voice_notes_dir() / f"note-{now:%Y%m%d-%H%M%S}.wav"
        await asyncio.to_thread(_write_wav, dest, pcm, self.capturer.sample_rate)
        self.brain.memory.add_voice_note(
            dest, now=now, duration_s=len(pcm) / self.capturer.sample_rate)
        log.info("voice-note saved: %s", dest.name)
        async with self._audio:
            await self._say("Zapisałam notatkę głosową.", "pl")

    # -- due reminders ---------------------------------------------------
    async def fire_due_reminders(self) -> list[Reply]:
        """Speak any reminders/alarms/events that have come due."""
        replies = self.brain.due_reminders(self.clock())
        for reminder in replies:
            log.info("reminder fired: %r", reminder.text)
            async with self._audio:
                await self._say(reminder.text, reminder.language)
        return replies

    async def _say(self, text: str, language: str) -> None:
        if text.strip():
            try:
                await asyncio.to_thread(self.sink.speak, text, language)
            except Exception as e:  # a TTS failure must not kill the loop
                log.warning("TTS speak failed (%s); continuing", e)

    # -- loops -----------------------------------------------------------
    async def run(
        self,
        wake: AsyncIterable[Any],
        *,
        stop: asyncio.Event,
        triggers: asyncio.Queue[npt.NDArray[np.int16]] | None = None,
    ) -> None:
        """Drive the runner until `stop` is set.

        Producers (the wake listener and any button thread feeding `triggers`)
        enqueue captured PCM; the reminder ticker fires on its own; the consumer
        routes each capture through :meth:`handle_pcm`.
        """
        queue = triggers if triggers is not None else asyncio.Queue()
        self.status_led.set(GREEN)  # listening for the wake word
        producers = [
            asyncio.create_task(self._wake_loop(wake, queue, stop)),
            asyncio.create_task(self._reminder_loop(stop)),
        ]
        try:
            while not stop.is_set():
                try:
                    pcm = await asyncio.wait_for(queue.get(), timeout=0.25)
                except TimeoutError:
                    continue
                await self.handle_pcm(pcm)
        finally:
            self.status_led.set(OFF)  # asleep
            self.radio.stop()
            for task in producers:
                task.cancel()
            await asyncio.gather(*producers, return_exceptions=True)

    async def _wake_loop(
        self,
        wake: AsyncIterable[Any],
        queue: asyncio.Queue[npt.NDArray[np.int16]],
        stop: asyncio.Event,
    ) -> None:
        async for env in wake:
            if stop.is_set():
                break
            if getattr(env, "topic", None) != "wake.detected":
                continue
            log.info("wake score=%s", getattr(env, "data", {}).get("score"))
            self.status_led.set(BLUE)  # wake detected → capturing the utterance
            # Acknowledge first so the capture window starts AFTER the cue and
            # the command isn't clipped by Jessica's own voice.
            async with self._audio:
                try:
                    await asyncio.to_thread(self.sink.acknowledge)
                except Exception as e:  # a TTS failure must not kill the loop
                    log.warning("ack TTS failed (%s); continuing", e)
            pcm = await self.capturer.capture(self.capture_s)
            await queue.put(pcm)

    async def _reminder_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=1.0)
            except TimeoutError:
                pass
            await self.fire_due_reminders()


def _write_wav(path: Path, pcm: npt.NDArray[np.int16], rate: int) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(np.asarray(pcm, dtype="<i2").tobytes())


# -- production seam implementations ----------------------------------------
class PiperSink:
    """Synthesise with Piper and play over ALSA (`aplay -D`). The wake
    acknowledgement is synthesised once and replayed for low latency."""

    def __init__(
        self,
        *,
        piper: str,
        voices: dict[str, str],
        out_device: str,
        ack_lang: str = "pl",
    ) -> None:
        self._piper = piper
        self._voices = voices
        self._out = out_device
        self._ack_lang = ack_lang
        self._ack_wav: str | None = None

    def _voice(self, language: str) -> str:
        return self._voices.get(language, self._voices["pl"])

    def speak(self, text: str, language: str) -> None:
        out = tempfile.mktemp(suffix=".wav")
        subprocess.run(
            [self._piper, "-m", self._voice(language), "-f", out],
            input=text.encode(), check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self.play_wav(out)

    def acknowledge(self) -> None:
        if self._ack_wav is None:
            self._ack_wav = tempfile.mktemp(suffix=".wav")
            subprocess.run(
                [self._piper, "-m", self._voice(self._ack_lang), "-f", self._ack_wav],
                input=ACK_TEXT[self._ack_lang].encode(), check=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        self.play_wav(self._ack_wav)

    def play_wav(self, path: str | Path) -> None:
        subprocess.run(["aplay", "-D", self._out, "-q", str(path)], check=False)


class NullRadio:
    """No-op radio for tests / the VM (records the requested station)."""

    def __init__(self) -> None:
        self.now_playing: str | None = None
        self.plays: list[str] = []

    def play(self, url: str, name: str = "") -> None:
        self.now_playing = name or url
        self.plays.append(url)

    def stop(self) -> None:
        self.now_playing = None


class StreamPlayer:
    """Streams an internet-radio URL *or* a local recording to an ALSA device
    via the ``blazend-player`` Rust unit.

    Replaces the old ffmpeg subprocess: ``blazend-player`` decodes
    (mp3/aac/flac/ogg/wav) into a prebuffered jitter buffer before the ALSA
    write loop, so low-bitrate / jittery streams play without the underrun
    stutter ffmpeg produced. Same contract: one source at a time
    (:meth:`play` stops any current one first), and the decoder runs in its own
    session so :meth:`stop` signals the whole group.
    """

    def __init__(
        self,
        *,
        device: str,
        player: str = "blazend-player",
        prebuffer_ms: int = 1500,
        buffer_ms: int = 4000,
    ) -> None:
        self._device = device
        self._player = player
        self._prebuffer_ms = prebuffer_ms
        self._buffer_ms = buffer_ms
        self._proc: subprocess.Popen[bytes] | None = None
        self.now_playing: str | None = None

    def play(self, url: str, name: str = "") -> None:
        self.stop()
        try:
            self._proc = subprocess.Popen(
                [self._player, "--source", url, "--device", self._device,
                 "--prebuffer-ms", str(self._prebuffer_ms),
                 "--buffer-ms", str(self._buffer_ms)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True,
            )
            self.now_playing = name or url
        except Exception as e:  # noqa: BLE001 — a dead player must not kill the loop
            log.warning("playback failed to start (%s)", e)
            self._proc = None
            self.now_playing = None

    def stop(self) -> None:
        proc = self._proc
        self._proc = None
        self.now_playing = None
        if proc is not None and proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):  # pragma: no cover
                pass


# Back-compat alias: the radio path is now the Rust stream/file player.
FfmpegRadio = StreamPlayer


class RingCapturer:
    """Capture a fresh window of PCM that arrives on the ring after the call
    starts — the live audio-in unit keeps writing while we wait."""

    def __init__(self, ring: RingReader) -> None:
        self._ring = ring

    @property
    def sample_rate(self) -> int:
        return int(self._ring.sample_rate)

    async def capture(self, seconds: float) -> npt.NDArray[np.int16]:
        start = self._ring.write_pos
        await asyncio.sleep(seconds)
        return self._ring.read_range(start, self._ring.write_pos)


# -- production wiring ------------------------------------------------------
def build_runner(
    *,
    capturer: Capturer,
    sink: AudioSink,
    capture_s: float | None = None,
    status_led: StatusLed | None = None,
    radio: RadioController | None = None,
) -> VoiceRunner:
    """Build a fully-wired runner (real engine + ASR). `capturer`/`sink` are
    passed in because they own hardware handles the caller sets up; the HAT's
    APA102 status LED is opened here (no-op when SPI is absent). `radio` is the
    caller's stream player (it owns the ALSA device); defaults to a no-op."""
    from blazend.config import load
    from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.gemini import GeminiClient
    from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.openai import OpenAiClient
    from blazend.domains.context.adapters.rpi5.embeddings import Embedder
    from blazend.domains.context.adapters.rpi5.memory import MemoryStore
    from blazend.domains.local_ai.adapters.rpi5.localllm import LocalLlm
    from blazend.domains.systems.adapters.rpi5.led_hw import open_status_led
    from blazend.domains.voice_input.adapters.rpi5.asr.engine import Transcriber

    data_dir = os.environ.get("BLAZEN_DATA_DIR")
    memory = MemoryStore(Path(data_dir) / "memory.json" if data_dir else None)
    # On-device semantic note recall. Falls back to lexical (embedder=None) when
    # disabled in config or when the model/deps are absent (Embedder.available).
    emb_cfg = load("embeddings")
    nc = emb_cfg.get("notes_context", {}) or {}
    embedder = Embedder(config=emb_cfg) if nc.get("enabled", True) else None
    # The Rust wake unit has already gated on "Hej Jessico", so a captured
    # utterance is by definition addressed to Jessica → always_awake.
    brain = Assistant(
        memory=memory, gemini=GeminiClient(), llm=LocalLlm(),
        openai=OpenAiClient(), embedder=embedder, always_awake=True,
        notes_top_k=int(nc.get("top_k", 4)),
        notes_min_score=float(nc.get("min_score", 0.82)),
        notes_rel_margin=float(nc.get("rel_margin", 0.06)),
        notes_max_chars=int(nc.get("max_chars", 1200)),
    )
    if capture_s is None:
        capture_s = float(
            os.environ.get("BLAZEN_CAPTURE_S")
            or load("wake-word").get("capture_window_s", 4.5)
        )
    return VoiceRunner(
        brain=brain, transcriber=Transcriber(), capturer=capturer,
        sink=sink, capture_s=capture_s,
        status_led=status_led if status_led is not None else open_status_led(),
        radio=radio if radio is not None else NullRadio(),
    )
