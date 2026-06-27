"""Tier 1 — production seams (PiperSink, StreamPlayer, build_runner) and the
``python -m blazend.domains.voice_input.adapters.rpi5.voice`` entrypoint wiring, all driven with fakes.

No real subprocess, ALSA, SPI, sockets, models, or network: every I/O boundary
is monkeypatched so the orchestration runs deterministically on a dev host.
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import numpy as np
import pytest

import blazend.domains.voice_input.adapters.rpi5.voice.__main__ as voice_main
from blazend.domains.voice_input.adapters.rpi5.voice import runner as runner_mod
from blazend.domains.voice_input.adapters.rpi5.voice.runner import (
    PiperSink,
    StreamPlayer,
    build_runner,
)
from blazend.events import Envelope

REPO = Path(__file__).resolve().parents[3]


# -- PiperSink --------------------------------------------------------------
class _RecPopenRun:
    """Records subprocess.run invocations (piper synth + aplay)."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, args, **kwargs):
        self.calls.append(list(args))

        class _Done:
            returncode = 0

        return _Done()


def test_piper_sink_speak_synths_then_plays(monkeypatch):
    rec = _RecPopenRun()
    monkeypatch.setattr(runner_mod.subprocess, "run", rec)
    monkeypatch.setattr(runner_mod.tempfile, "mktemp", lambda suffix="": "/tmp/x.wav")
    sink = PiperSink(
        piper="/bin/piper",
        voices={"pl": "/v/pl.onnx", "en": "/v/en.onnx"},
        out_device="plughw:0,0",
    )
    sink.speak("Cześć", "pl")
    # First call synthesises with the pl voice, second plays via aplay.
    assert rec.calls[0][0] == "/bin/piper" and "/v/pl.onnx" in rec.calls[0]
    assert rec.calls[1][0] == "aplay" and rec.calls[1][-1] == "/tmp/x.wav"


def test_piper_sink_falls_back_to_pl_voice_for_unknown_lang(monkeypatch):
    rec = _RecPopenRun()
    monkeypatch.setattr(runner_mod.subprocess, "run", rec)
    monkeypatch.setattr(runner_mod.tempfile, "mktemp", lambda suffix="": "/tmp/x.wav")
    sink = PiperSink(piper="/bin/piper", voices={"pl": "/v/pl.onnx"}, out_device="d")
    sink.speak("hi", "de")  # de unknown → pl voice
    assert "/v/pl.onnx" in rec.calls[0]


def test_piper_sink_acknowledge_synthesises_once_then_replays(monkeypatch):
    rec = _RecPopenRun()
    monkeypatch.setattr(runner_mod.subprocess, "run", rec)
    monkeypatch.setattr(runner_mod.tempfile, "mktemp", lambda suffix="": "/tmp/ack.wav")
    sink = PiperSink(
        piper="/bin/piper", voices={"pl": "/v/pl.onnx"}, out_device="d", ack_lang="pl",
    )
    sink.acknowledge()
    sink.acknowledge()
    piper_calls = [c for c in rec.calls if c[0] == "/bin/piper"]
    aplay_calls = [c for c in rec.calls if c[0] == "aplay"]
    assert len(piper_calls) == 1   # synthesised once
    assert len(aplay_calls) == 2   # replayed twice


# -- StreamPlayer -----------------------------------------------------------
class _FakeProc:
    def __init__(self, pid: int = 4321, alive: bool = True) -> None:
        self.pid = pid
        self._alive = alive

    def poll(self):
        return None if self._alive else 0


def test_stream_player_play_spawns_then_stop_signals_group(monkeypatch):
    spawned: list[list[str]] = []
    killed: list[int] = []
    proc = _FakeProc(pid=999)

    def _fake_popen(args, **kwargs):
        spawned.append(list(args))
        return proc

    monkeypatch.setattr(runner_mod.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(runner_mod.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(runner_mod.os, "killpg", lambda pgid, sig: killed.append(pgid))

    player = StreamPlayer(device="hw:0", player="blazend-player")
    player.play("http://stream/trojka", name="Trójka")
    assert player.now_playing == "Trójka"
    assert spawned[0][0] == "blazend-player"
    assert "http://stream/trojka" in spawned[0]

    player.stop()
    assert player.now_playing is None
    assert killed == [999]   # SIGTERM to the player's process group


def test_stream_player_play_replaces_current_source(monkeypatch):
    killed: list[int] = []
    procs = [_FakeProc(pid=1), _FakeProc(pid=2)]

    def _fake_popen(args, **kwargs):
        return procs.pop(0)

    monkeypatch.setattr(runner_mod.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(runner_mod.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(runner_mod.os, "killpg", lambda pgid, sig: killed.append(pgid))

    player = StreamPlayer(device="hw:0")
    player.play("url1", "One")
    player.play("url2", "Two")  # stops url1 first
    assert killed == [1]
    assert player.now_playing == "Two"


def test_stream_player_play_failure_is_swallowed(monkeypatch):
    def _boom(args, **kwargs):
        raise OSError("no such binary")

    monkeypatch.setattr(runner_mod.subprocess, "Popen", _boom)
    player = StreamPlayer(device="hw:0", player="missing-player")
    player.play("url", "X")  # must not raise
    assert player.now_playing is None
    player.stop()  # stop with no proc is a no-op


def test_stream_player_stop_noop_when_proc_already_exited(monkeypatch):
    killed: list[int] = []
    monkeypatch.setattr(runner_mod.subprocess, "Popen", lambda a, **k: _FakeProc(alive=False))
    monkeypatch.setattr(runner_mod.os, "killpg", lambda pgid, sig: killed.append(pgid))
    player = StreamPlayer(device="hw:0")
    player.play("url", "X")
    player.stop()
    assert killed == []  # poll() != None → nothing to kill


# -- build_runner -----------------------------------------------------------
class _FakeCapturer:
    sample_rate = 16_000

    async def capture(self, seconds):
        return np.zeros(16, dtype=np.int16)


class _FakeSink:
    def speak(self, text, language): ...
    def acknowledge(self): ...
    def play_wav(self, path): ...


def test_build_runner_wires_engine_and_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("BLAZEN_CONFIG_ROOT", str(REPO / "configs"))
    monkeypatch.setenv("BLAZEN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BLAZEN_LED", "0")  # NullStatusLed
    monkeypatch.delenv("BLAZEN_CAPTURE_S", raising=False)
    sink = _FakeSink()
    cap = _FakeCapturer()
    runner = build_runner(capturer=cap, sink=sink)
    assert runner.capturer is cap
    assert runner.sink is sink
    assert runner.capture_s > 0
    # Default radio is a no-op recorder.
    assert isinstance(runner.radio, runner_mod.NullRadio)


def test_build_runner_honours_explicit_capture_s_and_radio(monkeypatch, tmp_path):
    monkeypatch.setenv("BLAZEN_CONFIG_ROOT", str(REPO / "configs"))
    monkeypatch.setenv("BLAZEN_LED", "0")
    radio = runner_mod.NullRadio()
    runner = build_runner(
        capturer=_FakeCapturer(), sink=_FakeSink(), capture_s=2.5,
        status_led=runner_mod.NullStatusLed(), radio=radio,
    )
    assert runner.capture_s == 2.5
    assert runner.radio is radio


# -- __main__ wiring --------------------------------------------------------
class _FakeSubscriber:
    """A wake socket that yields one wake event, then blocks until cancelled."""

    def __init__(self, socket_path) -> None:
        self.socket_path = socket_path
        self.connected = False
        self.closed = False
        self._yielded = False

    async def connect(self):
        self.connected = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._yielded:
            self._yielded = True
            return Envelope(topic="wake.detected", source="blazend-wake", data={"score": 0.9})
        await asyncio.sleep(3600)
        raise StopAsyncIteration

    async def close(self):
        self.closed = True


class _FakeRing:
    sample_rate = 16_000
    write_pos = 0

    def __init__(self, path) -> None:
        self.path = path
        self.closed = False

    def read_range(self, a, b):
        return np.zeros(16, dtype=np.int16)

    def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_main_processes_a_wake_event_then_exits_cleanly(monkeypatch, tmp_path):
    monkeypatch.setenv("BLAZEN_CONFIG_ROOT", str(REPO / "configs"))
    monkeypatch.setenv("BLAZEN_BUTTON", "0")    # no GPIO thread
    monkeypatch.setenv("BLAZEN_LED", "0")
    monkeypatch.setenv("BLAZEN_DATA_DIR", str(tmp_path))

    spoken: list[str] = []

    class _RecordingSink(_FakeSink):
        def speak(self, text, language):
            spoken.append(text)

    sub = _FakeSubscriber(tmp_path / "wake.sock")
    ring = _FakeRing(tmp_path / "audio-ring.shm")

    monkeypatch.setattr(voice_main, "runtime_dir", lambda: tmp_path)
    monkeypatch.setattr(voice_main, "Subscriber", lambda path: sub)
    monkeypatch.setattr(voice_main, "RingReader", lambda path: ring)
    monkeypatch.setattr(voice_main, "RingCapturer", lambda r: _FakeCapturer())
    monkeypatch.setattr(voice_main, "PiperSink", lambda **kw: _RecordingSink())
    monkeypatch.setattr(voice_main, "StreamPlayer", lambda **kw: runner_mod.NullRadio())

    # Stop the runner shortly after it has acknowledged the wake so main()
    # falls through to its finally-block (close sub + ring) cleanly.
    real_build = voice_main.build_runner
    captured = {}

    def _build(**kw):
        r = real_build(**kw)
        # Make the captured utterance a deterministic, deaf transcript so the
        # route is cheap and never blocks; speaking is recorded above.
        from blazend.domains.voice_input.adapters.rpi5.asr.engine import Transcript

        class _T:
            language_mode = "auto"

            def transcribe(self, pcm, sample_rate=16_000):
                return Transcript(language="pl", text="która godzina", confidence=0.9)

        r.transcriber = _T()
        captured["runner"] = r
        return r

    monkeypatch.setattr(voice_main, "build_runner", _build)

    async def _drive():
        # Let main() spin up, process the wake event, then end the event stream.
        for _ in range(200):
            if captured.get("runner") and captured["runner"].status_led is not None:
                # once the sink has spoken, the wake turn completed
                if spoken:
                    break
            await asyncio.sleep(0.02)
        # Trigger a clean shutdown by raising CancelledError into main().
        main_task.cancel()

    main_task = asyncio.create_task(voice_main.main())
    driver = asyncio.create_task(_drive())
    with pytest.raises(asyncio.CancelledError):
        await main_task
    await driver

    assert sub.connected and sub.closed
    assert ring.closed
    assert spoken  # the wake turn ran end-to-end (ack + a time reply)


@pytest.mark.asyncio
async def test_main_spawns_button_thread_when_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("BLAZEN_CONFIG_ROOT", str(REPO / "configs"))
    monkeypatch.setenv("BLAZEN_BUTTON", "1")   # enable the PTT thread
    monkeypatch.setenv("BLAZEN_LED", "0")
    monkeypatch.setenv("BLAZEN_DATA_DIR", str(tmp_path))

    sub = _FakeSubscriber(tmp_path / "wake.sock")
    # No wake events — keep the loop idle so we just verify the thread spawn.
    sub._yielded = True
    ring = _FakeRing(tmp_path / "audio-ring.shm")

    spawned: list = []

    class _FakeThread:
        def __init__(self, *, target, args, daemon):
            spawned.append((target, args, daemon))

        def start(self):
            pass

    monkeypatch.setattr(voice_main.threading, "Thread", _FakeThread)
    monkeypatch.setattr(voice_main, "runtime_dir", lambda: tmp_path)
    monkeypatch.setattr(voice_main, "Subscriber", lambda path: sub)
    monkeypatch.setattr(voice_main, "RingReader", lambda path: ring)
    monkeypatch.setattr(voice_main, "RingCapturer", lambda r: _FakeCapturer())
    monkeypatch.setattr(voice_main, "PiperSink", lambda **kw: _FakeSink())
    monkeypatch.setattr(voice_main, "StreamPlayer", lambda **kw: runner_mod.NullRadio())

    main_task = asyncio.create_task(voice_main.main())
    await asyncio.sleep(0.1)
    main_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await main_task

    assert len(spawned) == 1                  # button thread was started
    assert spawned[0][0] is voice_main._button_loop
    assert spawned[0][2] is True              # daemon
    assert sub.closed and ring.closed


def test_voices_maps_pl_and_en(monkeypatch, tmp_path):
    monkeypatch.setenv("BLAZEN_CONFIG_ROOT", str(REPO / "configs"))
    monkeypatch.setenv("BLAZEN_MODELS_DIR", str(tmp_path / "models"))
    voices = voice_main._voices()
    assert set(voices) == {"pl", "en"}
    assert voices["pl"].endswith(".onnx") and "tts" in voices["pl"]
    assert voices["en"].endswith(".onnx")


def test_button_loop_captures_held_window(monkeypatch):
    """The PTT fallback: a falling/rising edge brackets a ring read enqueued
    onto the asyncio queue via call_soon_threadsafe."""
    edges: list[str] = []

    def _fake_wait_edge(edge):
        edges.append(edge)

    monkeypatch.setattr(voice_main, "_wait_edge", _fake_wait_edge)

    ring = _FakeRing(Path("/x"))
    pushed: list = []

    class _Loop:
        def call_soon_threadsafe(self, fn, arg):
            fn(arg)

    class _Q:
        def put_nowait(self, item):
            pushed.append(item)

    import threading

    stop = threading.Event()
    orig = ring.read_range

    def _read_then_stop(a, b):
        stop.set()  # one iteration only
        return orig(a, b)

    ring.read_range = _read_then_stop  # type: ignore[method-assign]
    voice_main._button_loop(ring, _Loop(), _Q(), stop)
    assert edges == ["falling", "rising"]
    assert len(pushed) == 1


def test_wait_edge_invokes_gpiomon(monkeypatch):
    calls: list[list[str]] = []

    def _run(args, **kwargs):
        calls.append(list(args))

        class _Done:
            returncode = 0

        return _Done()

    monkeypatch.setattr(subprocess, "run", _run)
    voice_main._wait_edge("falling")
    assert calls[0][0] == "gpiomon"
    assert "falling" in calls[0]
