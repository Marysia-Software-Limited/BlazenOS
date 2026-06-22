"""Tier 1 — the hands-free voice runner (S5) over its injected seams.

Drives :class:`blazend.voice.runner.VoiceRunner` with a fake `wake.detected`
source, a real pre-filled shared-memory ring, and fake ASR/TTS backends, so the
whole wake → capture → transcribe → route → speak chain runs deterministically
with no audio hardware. The engine is keyless (offline, deterministic paths).
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pytest

from blazend import led
from blazend.asr.engine import Transcript
from blazend.assistant.engine import Assistant
from blazend.assistant.gemini import GeminiClient
from blazend.assistant.localllm import LocalLlm
from blazend.assistant.memory import MemoryStore
from blazend.audio import RingReader, RingWriter
from blazend.events import Envelope
from blazend.voice.runner import NullRadio, VoiceRunner

REPO = Path(__file__).resolve().parents[3]


class FakeSink:
    """Records what the runner would speak instead of touching ALSA."""

    def __init__(self) -> None:
        self.spoken: list[tuple[str, str]] = []
        self.acks = 0
        self.played: list[str] = []

    def speak(self, text: str, language: str) -> None:
        self.spoken.append((language, text))

    def acknowledge(self) -> None:
        self.acks += 1

    def play_wav(self, path: str | Path) -> None:
        self.played.append(str(path))

    def said(self) -> str:
        return " | ".join(t for _, t in self.spoken)


class FakeTranscriber:
    """Returns a fixed transcript regardless of the PCM handed in."""

    def __init__(self, text: str, language: str = "pl") -> None:
        self.language_mode = "auto"
        self._text = text
        self._language = language

    def transcribe(
        self, pcm: npt.NDArray[np.int16] | npt.NDArray[np.float32], sample_rate: int = 16_000
    ) -> Transcript:
        return Transcript(language=self._language, text=self._text, confidence=0.9)


class RingSliceCapturer:
    """Hand back everything currently in the (pre-filled) ring."""

    def __init__(self, ring: RingReader) -> None:
        self._ring = ring

    @property
    def sample_rate(self) -> int:
        return self._ring.sample_rate

    async def capture(self, seconds: float) -> npt.NDArray[np.int16]:
        return self._ring.read_range(0, self._ring.write_pos)


class RecordingLed:
    """Captures the status-LED colour sequence the runner drives."""

    def __init__(self) -> None:
        self.color = led.OFF
        self.seq: list[str] = []

    def set(self, color: str) -> None:
        self.color = color
        self.seq.append(color)

    def close(self) -> None:
        self.color = led.OFF


class FakeWake:
    """Async wake source: yield the given envelopes, then block (until the
    runner cancels us on stop) so the consumer loop stays alive."""

    def __init__(self, envelopes: list[Envelope]) -> None:
        self._envs = list(envelopes)

    def __aiter__(self) -> AsyncIterator[Envelope]:
        return self

    async def __anext__(self) -> Envelope:
        if self._envs:
            return self._envs.pop(0)
        await asyncio.sleep(3600)
        raise StopAsyncIteration


def _engine(tmp_path: Path) -> Assistant:
    return Assistant(
        memory=MemoryStore(tmp_path / "mem.json"),
        gemini=GeminiClient(api_key=""),  # keyless → deterministic offline paths
        always_awake=True,
    )


def _ring_with_speech(tmp_path: Path) -> RingReader:
    path = tmp_path / "audio-ring.shm"
    with RingWriter(path) as writer:
        writer.push(np.ones(8_000, dtype=np.int16) * 1_000)  # ~0.5 s of "audio"
    return RingReader(path)


async def _wait_until(predicate, *, timeout: float = 2.0) -> None:
    for _ in range(int(timeout / 0.02)):
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not met within timeout")


@pytest.mark.asyncio
async def test_handle_pcm_routes_command_and_speaks(tmp_path):
    ring = _ring_with_speech(tmp_path)
    sink = FakeSink()
    runner = VoiceRunner(
        brain=_engine(tmp_path),
        transcriber=FakeTranscriber("zapamiętaj że kod do bramy to 4729"),
        capturer=RingSliceCapturer(ring),
        sink=sink,
        capture_s=0.01,
    )
    reply = await runner.handle_pcm(np.zeros(1_600, dtype=np.int16))
    assert reply is not None and reply.action == "note"
    assert "4729" in sink.said()
    ring.close()


@pytest.mark.asyncio
async def test_silence_is_ignored(tmp_path):
    sink = FakeSink()
    ring = _ring_with_speech(tmp_path)
    runner = VoiceRunner(
        brain=_engine(tmp_path),
        transcriber=FakeTranscriber(""),  # whisper heard nothing
        capturer=RingSliceCapturer(ring),
        sink=sink,
        capture_s=0.01,
    )
    assert await runner.handle_pcm(np.zeros(1_600, dtype=np.int16)) is None
    assert sink.spoken == []
    ring.close()


@pytest.mark.asyncio
async def test_wake_acknowledges_captures_and_replies(tmp_path):
    ring = _ring_with_speech(tmp_path)
    sink = FakeSink()
    runner = VoiceRunner(
        brain=_engine(tmp_path),
        transcriber=FakeTranscriber("zapamiętaj że spotkanie jest o piątej"),
        capturer=RingSliceCapturer(ring),
        sink=sink,
        capture_s=0.01,
    )
    stop = asyncio.Event()
    wake = FakeWake([Envelope(topic="wake.detected", source="blazend-wake", data={"score": 0.8})])
    task = asyncio.create_task(runner.run(wake, stop=stop))
    try:
        await _wait_until(lambda: bool(sink.spoken))
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=2.0)
    assert sink.acks == 1                       # acknowledged the wake first
    assert "spotkanie" in sink.said()           # then captured + answered
    ring.close()


@pytest.mark.asyncio
async def test_status_led_tracks_wake_capture_process_cycle(tmp_path):
    ring = _ring_with_speech(tmp_path)
    sink = FakeSink()
    status = RecordingLed()
    runner = VoiceRunner(
        brain=_engine(tmp_path),
        transcriber=FakeTranscriber("zapamiętaj że spotkanie jest o piątej"),
        capturer=RingSliceCapturer(ring),
        sink=sink,
        capture_s=0.01,
        status_led=status,
    )
    stop = asyncio.Event()
    wake = FakeWake([Envelope(topic="wake.detected", source="blazend-wake", data={"score": 0.8})])
    task = asyncio.create_task(runner.run(wake, stop=stop))
    try:
        await _wait_until(lambda: bool(sink.spoken))
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=2.0)
    # listening → capturing → processing → listening …, asleep on shutdown.
    assert status.seq[0] == led.GREEN
    assert led.BLUE in status.seq and led.MAGENTA in status.seq
    assert status.seq.index(led.BLUE) < status.seq.index(led.MAGENTA)
    assert status.color == led.OFF
    ring.close()


class _StreamBackend:
    """Local LLM backend that streams a reply token-by-token."""

    def __init__(self, chunks: list[str]) -> None:
        self.chunks = chunks

    def generate(self, *, system: str, user: str) -> str:
        return "".join(self.chunks)

    def generate_stream(self, *, system: str, user: str):
        yield from self.chunks


@pytest.mark.asyncio
async def test_runner_speaks_streamed_sentences_as_they_complete(tmp_path):
    ring = _ring_with_speech(tmp_path)
    sink = FakeSink()
    brain = Assistant(
        memory=MemoryStore(tmp_path / "m.json"),
        gemini=GeminiClient(api_key=""),  # keyless → local LLM is the chat path
        llm=LocalLlm(backend=_StreamBackend(["Mam się ", "świetnie", ". ", "A Ty", "?"])),
        always_awake=True,
    )
    runner = VoiceRunner(
        brain=brain,
        transcriber=FakeTranscriber("jak się masz"),  # routes to freeform chat
        capturer=RingSliceCapturer(ring),
        sink=sink,
        capture_s=0.01,
    )
    reply = await runner.handle_pcm(np.zeros(1_600, dtype=np.int16))
    assert reply is not None and reply.data.get("streamed") is True
    # Each sentence was spoken as a separate utterance, in order — not one blob.
    assert sink.spoken == [("pl", "Mam się świetnie."), ("pl", "A Ty?")]
    ring.close()


@pytest.mark.asyncio
async def test_runner_starts_and_stops_radio(tmp_path, monkeypatch):
    monkeypatch.setenv("BLAZEN_CONFIG_ROOT", str(REPO / "configs"))
    ring = _ring_with_speech(tmp_path)
    sink = FakeSink()
    radio = NullRadio()
    brain = Assistant(
        memory=MemoryStore(tmp_path / "m.json"),
        gemini=GeminiClient(api_key=""), always_awake=True,
    )
    runner = VoiceRunner(
        brain=brain, transcriber=FakeTranscriber("włącz Trójkę"),
        capturer=RingSliceCapturer(ring), sink=sink, capture_s=0.01, radio=radio,
    )
    reply = await runner.handle_pcm(np.zeros(1_600, dtype=np.int16))
    assert reply.action == "radio_play"
    assert radio.now_playing == "Trójka"
    assert any("polskieradio" in u for u in radio.plays)

    # A follow-up command frees the speaker (stops the radio) to answer.
    runner.transcriber = FakeTranscriber("wyłącz radio")
    reply = await runner.handle_pcm(np.zeros(1_600, dtype=np.int16))
    assert reply.action == "radio_stop" and radio.now_playing is None
    ring.close()


class _CapturingLlmBackend:
    """Records the system prompt the engine builds (to assert note injection)."""

    def __init__(self) -> None:
        self.last_system: str | None = None

    def generate(self, *, system: str, user: str) -> str:
        self.last_system = system
        return "ok"


class _FakeEmbedder:
    """2-axis deterministic embedder: weekend vs. everything else."""

    name = "fake-emb"
    available = True

    def embed(self, texts, *, kind="passage"):
        return [[1.0 if ("weekend" in t.lower() or "gór" in t.lower()) else 0.0, 0.1] for t in texts]


class _BoomSink(FakeSink):
    """A sink whose TTS always fails (the live pl_PL-darkman Piper crash)."""

    def speak(self, text: str, language: str) -> None:
        raise RuntimeError("piper exited 1")


@pytest.mark.asyncio
async def test_held_button_titled_note_then_context_injected(tmp_path):
    # 1) Dictate a long titled note (held-button capture → one transcript).
    ring = _ring_with_speech(tmp_path)
    backend = _CapturingLlmBackend()
    brain = Assistant(
        memory=MemoryStore(tmp_path / "mem.json"),
        gemini=GeminiClient(api_key=""),
        llm=LocalLlm(backend=backend),
        embedder=_FakeEmbedder(),
        always_awake=True,
    )
    sink = FakeSink()
    note_runner = VoiceRunner(
        brain=brain,
        transcriber=FakeTranscriber("zapamiętaj: plan na weekend. Chcę pojechać w góry"),
        capturer=RingSliceCapturer(ring),
        sink=sink,
        capture_s=0.01,
    )
    r = await note_runner.handle_pcm(np.zeros(1_600, dtype=np.int16))
    assert r is not None and r.action == "note" and r.data["title"] == "plan na weekend"

    # 2) A later question retrieves that note into the LLM's system prompt.
    chat_runner = VoiceRunner(
        brain=brain,
        transcriber=FakeTranscriber("co planuję w weekend?"),
        capturer=RingSliceCapturer(ring),
        sink=sink,
        capture_s=0.01,
    )
    await chat_runner.handle_pcm(np.zeros(1_600, dtype=np.int16))
    assert "góry" in (backend.last_system or "")
    ring.close()


@pytest.mark.asyncio
async def test_tts_failure_does_not_crash_the_loop(tmp_path):
    ring = _ring_with_speech(tmp_path)
    runner = VoiceRunner(
        brain=_engine(tmp_path),
        transcriber=FakeTranscriber("zapamiętaj że kod do bramy to 4729"),
        capturer=RingSliceCapturer(ring),
        sink=_BoomSink(),
        capture_s=0.01,
    )
    # speak() raising must be swallowed — handle_pcm still returns the reply.
    reply = await runner.handle_pcm(np.zeros(1_600, dtype=np.int16))
    assert reply is not None and reply.action == "note"
    ring.close()


@pytest.mark.asyncio
async def test_voice_note_record_writes_wav(tmp_path):
    ring = _ring_with_speech(tmp_path)
    engine = _engine(tmp_path)
    sink = FakeSink()
    runner = VoiceRunner(
        brain=engine,
        transcriber=FakeTranscriber("nagraj notatkę głosową"),
        capturer=RingSliceCapturer(ring),
        sink=sink,
        capture_s=0.01,
    )
    reply = await runner.handle_pcm(np.zeros(1_600, dtype=np.int16))
    assert reply is not None and reply.action == "voice_note_record"
    notes = engine.memory.voice_notes()
    assert len(notes) == 1
    assert Path(notes[0].audio_path).exists()
    ring.close()
