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


class _FakeMemory:
    """Minimal MemoryStore stand-in for brain-less runner tests."""

    def __init__(self, tmp_path: Path) -> None:
        self._dir = tmp_path / "voice-notes"
        self._dir.mkdir(parents=True, exist_ok=True)
        self.added: list[Path] = []

    def voice_notes_dir(self) -> Path:
        return self._dir

    def add_voice_note(self, dest, *, now, duration_s) -> None:
        self.added.append(Path(dest))


class _ScriptedBrain:
    """A brain whose `route` returns a pre-canned Reply (and optionally drives
    the on_sentence/on_token hooks), so the runner's post-route branches can be
    exercised without leaning on the real engine's routing."""

    def __init__(self, reply, memory=None, *, sentences=None) -> None:
        self._reply = reply
        self.memory = memory
        self._sentences = sentences or []
        self.routed: list[str] = []

    def route(self, text, *, now, on_sentence=None, on_token=None):
        self.routed.append(text)
        if on_token is not None:
            on_token()
        for lang, sentence in self._sentences:
            if on_sentence is not None:
                on_sentence(sentence, lang)
        return self._reply

    def due_reminders(self, now):
        return []


@pytest.mark.asyncio
async def test_voice_note_play_plays_each_stored_wav(tmp_path):
    from blazend.assistant.engine import Reply

    ring = _ring_with_speech(tmp_path)
    sink = FakeSink()
    reply = Reply(
        "Mam dwie notatki.", "pl", "voice_note_play",
        {"paths": ["/notes/a.wav", "/notes/b.wav"]},
    )
    runner = VoiceRunner(
        brain=_ScriptedBrain(reply, _FakeMemory(tmp_path)),
        transcriber=FakeTranscriber("odtwórz moje notatki"),
        capturer=RingSliceCapturer(ring),
        sink=sink,
        capture_s=0.01,
    )
    out = await runner.handle_pcm(np.zeros(1_600, dtype=np.int16))
    assert out is reply
    assert sink.played == ["/notes/a.wav", "/notes/b.wav"]
    ring.close()


@pytest.mark.asyncio
async def test_error_reply_is_spoken_not_streamed(tmp_path):
    from blazend.assistant.engine import Reply

    ring = _ring_with_speech(tmp_path)
    sink = FakeSink()
    status = RecordingLed()
    reply = Reply("Coś poszło nie tak.", "pl", "error")
    runner = VoiceRunner(
        brain=_ScriptedBrain(reply, _FakeMemory(tmp_path)),
        transcriber=FakeTranscriber("jaka jest pogoda"),
        capturer=RingSliceCapturer(ring),
        sink=sink,
        capture_s=0.01,
        status_led=status,
    )
    out = await runner.handle_pcm(np.zeros(1_600, dtype=np.int16))
    assert out.action == "error"
    # A non-streamed error reply is spoken as one utterance.
    assert sink.spoken == [("pl", "Coś poszło nie tak.")]
    # NOTE: the runner does not paint RED on error — its state palette is
    # green/blue/magenta/off only. After processing it returns to GREEN.
    assert status.color == led.GREEN
    ring.close()


@pytest.mark.asyncio
async def test_fire_due_reminders_speaks_each(tmp_path):
    from blazend.assistant.engine import Reply

    ring = _ring_with_speech(tmp_path)
    sink = FakeSink()

    class _Due(_ScriptedBrain):
        def due_reminders(self, now):
            return [
                Reply("Alarm: piąta.", "pl", "reminder"),
                Reply("Wake up.", "en", "reminder"),
            ]

    runner = VoiceRunner(
        brain=_Due(Reply("", "pl"), _FakeMemory(tmp_path)),
        transcriber=FakeTranscriber("x"),
        capturer=RingSliceCapturer(ring),
        sink=sink,
        capture_s=0.01,
    )
    fired = await runner.fire_due_reminders()
    assert len(fired) == 2
    assert sink.spoken == [("pl", "Alarm: piąta."), ("en", "Wake up.")]
    ring.close()


@pytest.mark.asyncio
async def test_streamed_path_survives_tts_failure_midstream(tmp_path):
    """A speak() that raises inside on_sentence must not kill the stream — the
    next sentence still drives on_sentence (warning logged, loop continues)."""
    from blazend.assistant.engine import Reply

    ring = _ring_with_speech(tmp_path)

    calls: list[str] = []

    class _OneBoomSink(FakeSink):
        def speak(self, text, language):
            calls.append(text)
            if len(calls) == 1:
                raise RuntimeError("piper exited 1")
            super().speak(text, language)

    reply = Reply("Pierwsze. Drugie.", "pl", "chat", {"streamed": True})
    brain = _ScriptedBrain(
        reply, _FakeMemory(tmp_path),
        sentences=[("pl", "Pierwsze."), ("pl", "Drugie.")],
    )
    sink = _OneBoomSink()
    runner = VoiceRunner(
        brain=brain,
        transcriber=FakeTranscriber("jak się masz"),
        capturer=RingSliceCapturer(ring),
        sink=sink,
        capture_s=0.01,
    )
    out = await runner.handle_pcm(np.zeros(1_600, dtype=np.int16))
    assert out.data.get("streamed") is True
    # Both sentences attempted; the second succeeded despite the first crashing.
    assert calls == ["Pierwsze.", "Drugie."]
    assert sink.spoken == [("pl", "Drugie.")]
    ring.close()


@pytest.mark.asyncio
async def test_wake_loop_ignores_non_wake_envelopes(tmp_path):
    """An envelope on a different topic is dropped without acking/capturing."""
    ring = _ring_with_speech(tmp_path)
    sink = FakeSink()
    runner = VoiceRunner(
        brain=_engine(tmp_path),
        transcriber=FakeTranscriber("zapamiętaj że kod to 1"),
        capturer=RingSliceCapturer(ring),
        sink=sink,
        capture_s=0.01,
    )
    stop = asyncio.Event()
    wake = FakeWake([
        Envelope(topic="heartbeat", source="x", data={}),
        Envelope(topic="wake.detected", source="blazend-wake", data={"score": 0.9}),
    ])
    task = asyncio.create_task(runner.run(wake, stop=stop))
    try:
        await _wait_until(lambda: bool(sink.spoken))
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=2.0)
    # Exactly one ack — the heartbeat envelope did not trigger a capture.
    assert sink.acks == 1
    ring.close()


@pytest.mark.asyncio
async def test_wake_loop_swallows_ack_tts_failure(tmp_path):
    """acknowledge() raising must not kill the wake loop; capture still runs."""
    ring = _ring_with_speech(tmp_path)

    class _AckBoomSink(FakeSink):
        def acknowledge(self):
            self.acks += 1
            raise RuntimeError("ack piper died")

    sink = _AckBoomSink()
    runner = VoiceRunner(
        brain=_engine(tmp_path),
        transcriber=FakeTranscriber("zapamiętaj że kod to 7"),
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
    assert sink.acks == 1            # ack was attempted (and raised)
    assert "7" in sink.said()        # the command was still captured + answered
    ring.close()


@pytest.mark.asyncio
async def test_run_with_external_trigger_queue(tmp_path):
    """A button thread feeds PCM straight onto the triggers queue (no wake)."""
    ring = _ring_with_speech(tmp_path)
    sink = FakeSink()
    runner = VoiceRunner(
        brain=_engine(tmp_path),
        transcriber=FakeTranscriber("zapamiętaj że kod to 42"),
        capturer=RingSliceCapturer(ring),
        sink=sink,
        capture_s=0.01,
    )
    stop = asyncio.Event()
    triggers: asyncio.Queue = asyncio.Queue()
    triggers.put_nowait(np.zeros(1_600, dtype=np.int16))
    wake = FakeWake([])  # no wake events — only the button feeds the queue
    task = asyncio.create_task(runner.run(wake, stop=stop, triggers=triggers))
    try:
        await _wait_until(lambda: bool(sink.spoken))
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=2.0)
    assert "42" in sink.said()
    assert sink.acks == 0  # button path does not ack
    ring.close()


@pytest.mark.asyncio
async def test_reminder_loop_ticks_and_fires(tmp_path):
    """The reminder ticker wakes on its 1 s timeout and calls fire_due_reminders.

    We monkeypatch the wait timeout down so a tick lands inside the test window,
    exercising the loop's TimeoutError (no-stop) branch."""
    from blazend.assistant.engine import Reply

    ring = _ring_with_speech(tmp_path)
    sink = FakeSink()
    fired = asyncio.Event()

    class _Tick(_ScriptedBrain):
        def due_reminders(self, now):
            fired.set()
            return [Reply("Czas.", "pl", "reminder")]

    runner = VoiceRunner(
        brain=_Tick(Reply("", "pl"), _FakeMemory(tmp_path)),
        transcriber=FakeTranscriber(""),
        capturer=RingSliceCapturer(ring),
        sink=sink,
        capture_s=0.01,
    )
    # Shrink the reminder-loop poll interval so a tick happens promptly.
    real_wait_for = asyncio.wait_for

    async def _fast_wait_for(aw, timeout):
        if timeout == 1.0:
            timeout = 0.02
        return await real_wait_for(aw, timeout)

    stop = asyncio.Event()
    wake = FakeWake([])
    import blazend.voice.runner as rmod

    orig = rmod.asyncio.wait_for
    rmod.asyncio.wait_for = _fast_wait_for  # type: ignore[assignment]
    task = asyncio.create_task(runner.run(wake, stop=stop))
    try:
        await asyncio.wait_for(fired.wait(), timeout=2.0)
    finally:
        rmod.asyncio.wait_for = orig  # type: ignore[assignment]
        stop.set()
        await asyncio.wait_for(task, timeout=2.0)
    assert ("pl", "Czas.") in sink.spoken
    ring.close()


@pytest.mark.asyncio
async def test_wake_loop_breaks_when_stop_already_set(tmp_path):
    """A wake envelope arriving after stop is set is dropped (loop breaks)."""
    ring = _ring_with_speech(tmp_path)
    sink = FakeSink()
    runner = VoiceRunner(
        brain=_engine(tmp_path),
        transcriber=FakeTranscriber("zapamiętaj że kod to 9"),
        capturer=RingSliceCapturer(ring),
        sink=sink,
        capture_s=0.01,
    )
    stop = asyncio.Event()

    class _SlowWake:
        def __aiter__(self):
            return self

        async def __anext__(self):
            # Yield only after stop is set, so _wake_loop hits the break branch.
            await stop.wait()
            return Envelope(topic="wake.detected", source="w", data={"score": 1.0})

    task = asyncio.create_task(runner.run(_SlowWake(), stop=stop))
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, timeout=2.0)
    assert sink.acks == 0  # the post-stop wake event was not acted on
    ring.close()


@pytest.mark.asyncio
async def test_ring_capturer_reads_window_after_start(tmp_path):
    """RingCapturer (production seam): captures samples written during capture."""
    from blazend.audio import RingWriter
    from blazend.voice.runner import RingCapturer

    path = tmp_path / "live-ring.shm"
    with RingWriter(path) as writer:
        reader = RingReader(path)
        cap = RingCapturer(reader)
        assert cap.sample_rate == reader.sample_rate

        async def _writer_task():
            await asyncio.sleep(0.005)
            writer.push(np.ones(1_600, dtype=np.int16) * 500)

        producer = asyncio.create_task(_writer_task())
        pcm = await cap.capture(0.05)
        await producer
        assert pcm.shape[0] == 1_600
        reader.close()


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
