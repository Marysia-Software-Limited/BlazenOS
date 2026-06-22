"""Tier 1 — smoke/integration tests for the unit `__main__` entrypoints.

Each unit's `__main__` wires a Subscriber/Publisher over sockets, loads
models, and runs an asyncio loop. These tests drive `main()` / `run()` with
*all* externals mocked (no real sockets, models, network, audio, subprocess)
so we get legitimate integration coverage of the wiring while staying fast and
hermetic. The seams mocked here are exactly the ones the units degrade through.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import blazend.asr.__main__ as asr_main
import blazend.assistant.__main__ as assistant_main
import blazend.bootstrap.__main__ as bootstrap_main
import blazend.brain.__main__ as brain_main
import blazend.orchestrator.__main__ as orch_main
from blazend.events import Envelope
from blazend.ipc import Publisher

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _config_root(monkeypatch):
    monkeypatch.setenv("BLAZEN_CONFIG_ROOT", str(REPO / "configs"))


class FakePublisher:
    """Records published envelopes; no real socket."""

    def __init__(self, socket_path: Path) -> None:
        self._socket_path = socket_path
        self.published: list[Envelope] = []
        self.bound = False
        self.closed = False

    async def bind(self) -> None:
        self.bound = True

    async def publish(self, env: Envelope) -> None:
        self.published.append(env)

    async def close(self) -> None:
        self.closed = True


# ---------------------------------------------------------------------------
# blazend.asr.__main__
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_asr_run_mock_emits_final(monkeypatch, tmp_path):
    """`run(mock=True)` binds asr.sock, announces ready, then emits a final.

    We stub asyncio.sleep so the mock loop yields one utterance and then the
    second sleep raises to break the otherwise-infinite loop.
    """
    pub = FakePublisher(tmp_path / "asr.sock")
    monkeypatch.setattr(asr_main, "Publisher", lambda p: pub)
    monkeypatch.setattr(asr_main, "runtime_dir", lambda: tmp_path)

    calls = {"n": 0}

    async def fake_sleep(_secs: float) -> None:
        calls["n"] += 1
        if calls["n"] >= 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(asr_main.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await asr_main.run(mock=True)

    assert pub.bound
    topics = [e.topic for e in pub.published]
    assert "system.event" in topics
    assert "asr.final" in topics


def test_asr_main_invokes_run(monkeypatch):
    """`main()` parses argv and calls asyncio.run(run(mock))."""
    seen: dict[str, object] = {}

    async def fake_run(mock: bool) -> None:
        seen["mock"] = mock

    monkeypatch.setattr(asr_main, "run", fake_run)
    monkeypatch.setattr("sys.argv", ["blazend-asr", "--mock"])
    asr_main.main()
    assert seen["mock"] is True


@pytest.mark.asyncio
async def test_asr_open_ring_timeout(monkeypatch, tmp_path):
    """`_open_ring` raises TimeoutError when the ring never appears."""

    async def fast_sleep(_s: float) -> None:
        return None

    monkeypatch.setattr(asr_main.asyncio, "sleep", fast_sleep)
    with pytest.raises(TimeoutError):
        await asr_main._open_ring(tmp_path / "nope.shm", timeout=-1.0)


class _FakeRing:
    sample_rate = 16_000

    def __init__(self) -> None:
        self.write_pos = 1000

    def read_range(self, start, end):  # noqa: ANN001
        import numpy as np

        return np.zeros(end - start, dtype=np.int16)


class _FakeTranscript:
    def __init__(self, text: str) -> None:
        self.language = "pl"
        self.text = text
        self.confidence = 0.9


class _FakeTranscriber:
    model = "small"

    def transcribe(self, pcm, sample_rate):  # noqa: ANN001
        return _FakeTranscript("która godzina")


class _OneShotSub:
    """Async-iterable subscriber that yields vad.start then vad.end, then stops."""

    def __init__(self, envs: list[Envelope]) -> None:
        self._envs = iter(envs)

    def __aiter__(self):  # noqa: ANN204
        return self

    async def __anext__(self) -> Envelope:
        try:
            return next(self._envs)
        except StopIteration:
            raise StopAsyncIteration from None


@pytest.mark.asyncio
async def test_asr_real_loop_transcribes_and_publishes(monkeypatch, tmp_path):
    """`_real_loop`: vad.start/vad.end → transcribe → asr.final published."""
    import blazend.asr.engine as engine

    monkeypatch.setattr(engine, "Transcriber", _FakeTranscriber)
    monkeypatch.setattr(asr_main, "runtime_dir", lambda: tmp_path)

    async def fake_open_ring(_path, timeout=30.0):  # noqa: ANN001
        return _FakeRing()

    sub = _OneShotSub(
        [
            Envelope(topic="vad.start", source="audio", data={}),
            Envelope(topic="vad.end", source="audio", data={}),
        ]
    )

    async def fake_connect(_sock):  # noqa: ANN001
        return sub

    monkeypatch.setattr(asr_main, "_open_ring", fake_open_ring)
    monkeypatch.setattr(asr_main, "_connect", fake_connect)

    pub = FakePublisher(tmp_path / "asr.sock")
    await asr_main._real_loop(pub)

    topics = [e.topic for e in pub.published]
    assert "asr.final" in topics


@pytest.mark.asyncio
async def test_asr_connect_returns_subscriber(tmp_path):
    """`_connect` connects once the peer's socket exists (happy path)."""
    import tempfile

    rt = Path(tempfile.mkdtemp(prefix="bl-asr-", dir="/tmp"))
    peer = Publisher(rt / "audio-in.sock")
    await peer.bind()
    try:
        sub = await asyncio.wait_for(asr_main._connect(rt / "audio-in.sock"), timeout=2.0)
        await sub.close()
    finally:
        await peer.close()


@pytest.mark.asyncio
async def test_brain_connect_nlu_returns_subscriber(tmp_path):
    import tempfile

    rt = Path(tempfile.mkdtemp(prefix="bl-brain-", dir="/tmp"))
    peer = Publisher(rt / "nlu.sock")
    await peer.bind()
    try:
        sub = await asyncio.wait_for(brain_main._connect_nlu(rt), timeout=2.0)
        await sub.close()
    finally:
        await peer.close()


@pytest.mark.asyncio
async def test_asr_real_loop_no_text_emits_error(monkeypatch, tmp_path):
    """An empty transcript → an `error` envelope (asr.no_text)."""
    import blazend.asr.engine as engine

    class _Empty(_FakeTranscriber):
        def transcribe(self, pcm, sample_rate):  # noqa: ANN001
            return _FakeTranscript("")

    monkeypatch.setattr(engine, "Transcriber", _Empty)
    monkeypatch.setattr(asr_main, "runtime_dir", lambda: tmp_path)

    async def fake_open_ring(_path, timeout=30.0):  # noqa: ANN001
        return _FakeRing()

    async def fake_connect(_sock):  # noqa: ANN001
        return _OneShotSub(
            [
                Envelope(topic="vad.start", source="audio", data={}),
                Envelope(topic="vad.end", source="audio", data={}),
            ]
        )

    monkeypatch.setattr(asr_main, "_open_ring", fake_open_ring)
    monkeypatch.setattr(asr_main, "_connect", fake_connect)

    pub = FakePublisher(tmp_path / "asr.sock")
    await asr_main._real_loop(pub)
    assert [e.topic for e in pub.published] == ["error"]


# ---------------------------------------------------------------------------
# blazend.brain.__main__
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_brain_run_mock_emits_reply(monkeypatch, tmp_path):
    pub = FakePublisher(tmp_path / "brain.sock")
    monkeypatch.setattr(brain_main, "Publisher", lambda p: pub)
    monkeypatch.setattr(brain_main, "runtime_dir", lambda: tmp_path)

    calls = {"n": 0}

    async def fake_sleep(_secs: float) -> None:
        calls["n"] += 1
        if calls["n"] >= 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(brain_main.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await brain_main.run(mock=True)

    topics = [e.topic for e in pub.published]
    assert "system.event" in topics
    assert "brain.reply" in topics


@pytest.mark.asyncio
async def test_brain_run_real_builds_engine_and_serves(monkeypatch):
    """`run(mock=False)` reads wake config, builds LocalLlm + OpenAI, serves.

    We stub serve() and the two clients so no socket/model is touched, then
    assert the real wiring path (config read, Assistant construction) ran.
    """
    served: dict[str, object] = {}

    async def fake_serve(engine, **_kw) -> None:
        served["engine"] = engine

    monkeypatch.setattr(brain_main, "serve", fake_serve)
    monkeypatch.setattr(brain_main, "LocalLlm", lambda: object())
    monkeypatch.setattr(brain_main, "OpenAiClient", lambda: object())

    await brain_main.run(mock=False)
    assert served["engine"] is not None


@pytest.mark.asyncio
async def test_brain_run_real_degrades_when_localllm_raises(monkeypatch):
    """LocalLlm construction failure → llm=None, still serves (cloud fallback)."""
    served: dict[str, object] = {}

    async def fake_serve(engine, **_kw) -> None:
        served["engine"] = engine

    def boom() -> object:
        raise RuntimeError("no binding")

    monkeypatch.setattr(brain_main, "serve", fake_serve)
    monkeypatch.setattr(brain_main, "LocalLlm", boom)
    monkeypatch.setattr(brain_main, "OpenAiClient", lambda: object())

    await brain_main.run(mock=False)
    assert served["engine"] is not None


@pytest.mark.asyncio
async def test_brain_run_real_degrades_when_wake_config_unreadable(monkeypatch):
    served: dict[str, object] = {}

    async def fake_serve(engine, **_kw) -> None:
        served["engine"] = engine

    def bad_load(_name: str):
        raise RuntimeError("no config")

    monkeypatch.setattr(brain_main, "load", bad_load)
    monkeypatch.setattr(brain_main, "serve", fake_serve)
    monkeypatch.setattr(brain_main, "LocalLlm", lambda: object())
    monkeypatch.setattr(brain_main, "OpenAiClient", lambda: object())

    await brain_main.run(mock=False)
    assert served["engine"] is not None


def test_brain_main_invokes_run(monkeypatch):
    seen: dict[str, object] = {}

    async def fake_run(mock: bool) -> None:
        seen["mock"] = mock

    monkeypatch.setattr(brain_main, "run", fake_run)
    monkeypatch.setattr("sys.argv", ["blazend-brain"])
    brain_main.main()
    assert seen["mock"] is False


# ---------------------------------------------------------------------------
# blazend.orchestrator.__main__
# ---------------------------------------------------------------------------


def test_orchestrator_main_runs_and_returns(monkeypatch, tmp_path):
    """`main()` builds an Orchestrator and runs it to completion.

    We swap in a fake Orchestrator whose `run()` returns immediately so the
    asyncio.run + signal-handler wiring is exercised without a real loop.
    """

    class FakeOrch:
        def __init__(self) -> None:
            self.ran = False

        async def run(self) -> None:
            self.ran = True

        async def shutdown(self) -> None:
            pass

    fake = FakeOrch()
    monkeypatch.setattr(orch_main, "Orchestrator", lambda: fake)
    orch_main.main()
    assert fake.ran


# ---------------------------------------------------------------------------
# blazend.bootstrap.__main__
# ---------------------------------------------------------------------------


def test_bootstrap_no_firstboot_dir(monkeypatch, tmp_path):
    monkeypatch.setattr("sys.argv", ["blazend-bootstrap", "--boot-dir", str(tmp_path / "absent")])
    assert bootstrap_main.main() == 0


def test_bootstrap_dry_run(monkeypatch, tmp_path):
    boot = tmp_path / "boot"
    boot.mkdir()
    (boot / "blazen-bootstrap.yaml").write_text("x: 1", encoding="utf-8")
    etc = tmp_path / "etc"
    monkeypatch.setattr(
        "sys.argv",
        ["blazend-bootstrap", "--dry-run", "--boot-dir", str(boot), "--etc-dir", str(etc)],
    )
    assert bootstrap_main.main() == 0
    assert not etc.exists()  # dry-run never wrote


def test_bootstrap_seeds_etc_and_self_disables(monkeypatch, tmp_path):
    boot = tmp_path / "boot"
    boot.mkdir()
    (boot / "blazen-bootstrap.yaml").write_text("x: 1", encoding="utf-8")
    (boot / "authorized_keys").write_text("ssh-ed25519 AAAA operator", encoding="utf-8")
    (boot / "stray").mkdir()  # exercises the IsADirectoryError rmtree branch
    (boot / "stray" / "f").write_text("y", encoding="utf-8")
    etc = tmp_path / "etc"
    monkeypatch.setattr(
        "sys.argv",
        ["blazend-bootstrap", "--boot-dir", str(boot), "--etc-dir", str(etc)],
    )
    assert bootstrap_main.main() == 0
    assert (etc / "blazen-bootstrap.yaml").is_file()
    assert (etc / "authorized_keys").is_file()
    assert not boot.exists()  # firstboot dir removed (replay defence)


# ---------------------------------------------------------------------------
# blazend.assistant.__main__ (the Jessica REPL)
# ---------------------------------------------------------------------------


def test_assistant_main_once(monkeypatch, tmp_path, capsys):
    """`--once` routes a single utterance and exits 0, printing the reply."""
    monkeypatch.setenv("GEMINI_API_KEY", "")  # keyless → deterministic
    rc = assistant_main.main(
        [
            "--once",
            "zapamiętaj że kod do bramy to 4729",
            "--no-wake",
            "--data",
            str(tmp_path / "mem.json"),
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "Jessica:" in out
    assert "4729" in out


def test_assistant_main_interactive_eof(monkeypatch, tmp_path, capsys):
    """Interactive loop: one line then EOF → clean exit, banner + farewell."""
    monkeypatch.setenv("GEMINI_API_KEY", "")
    lines = iter(["co pamiętasz?"])

    def fake_input(_prompt: str = "") -> str:
        try:
            return next(lines)
        except StopIteration as e:
            raise EOFError from e

    monkeypatch.setattr("builtins.input", fake_input)
    # Keep the reminder watcher thread from ever ticking against the clock.
    monkeypatch.setattr(assistant_main.threading, "Thread", _NoopThread)

    rc = assistant_main.main(["--data", str(tmp_path / "mem.json"), "--no-wake"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Jessica (prototyp)" in out
    assert "Do usłyszenia" in out


def test_assistant_main_interactive_quit_command(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setattr("builtins.input", lambda _p="": "exit")
    monkeypatch.setattr(assistant_main.threading, "Thread", _NoopThread)
    assert assistant_main.main(["--data", str(tmp_path / "mem.json")]) == 0


class _NoopThread:
    """Thread stand-in that never actually starts the reminder watcher."""

    def __init__(self, *_a, **_kw) -> None:
        pass

    def start(self) -> None:
        pass
