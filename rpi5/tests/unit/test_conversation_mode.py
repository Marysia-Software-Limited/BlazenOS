"""Tier 0 — conversation mode (2026-08-30): multi-turn LLM history in the
engine, the ASR follow-up capture, and the orchestrator's follow-up arming.

Offline: fake router backends, fake ring/pub. PL + EN parity is not at stake
here (no new spoken strings — the follow-up cue is the existing chime).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.engine import Assistant
from blazend.domains.ai_orchestrator.core.model_router import Task
from blazend.domains.context.adapters.rpi5.memory import MemoryStore

NOW = datetime(2026, 8, 30, 12, 0)
REPO = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("BLAZEN_CONFIG_ROOT", str(REPO / "configs"))
    monkeypatch.setenv("BLAZEN_DATA_DIR", str(tmp_path))


class _HistBackend:
    """Fake router backend that records the history it was given."""

    available = True

    def __init__(self) -> None:
        self.calls: list[tuple[str, list | None]] = []

    def chat(self, text: str, *, system: str = "", history=None) -> str:
        self.calls.append((text, list(history) if history else None))
        return f"odp:{text}"


class _OneBackendRouter:
    def __init__(self, backend) -> None:
        self._b = backend

    def route(self, task: Task):
        return [("fake", self._b)]


def _assistant(tmp_path, backend) -> Assistant:
    a = Assistant(memory=MemoryStore(tmp_path / "memory.json"),
                  router=_OneBackendRouter(backend), always_awake=True)
    a._notes_context = lambda text, lang: ""  # offline: no embedder
    return a


def test_chat_carries_history_across_turns(tmp_path):
    b = _HistBackend()
    a = _assistant(tmp_path, b)
    a._chat("pierwsze pytanie", "pl")
    a._chat("drugie pytanie", "pl")
    assert b.calls[0] == ("pierwsze pytanie", None)  # first turn: no history
    assert b.calls[1][1] == [("pierwsze pytanie", "odp:pierwsze pytanie")]


def test_history_trims_to_turn_cap(tmp_path):
    b = _HistBackend()
    a = _assistant(tmp_path, b)
    a._history_turns = 2
    for i in range(4):
        a._chat(f"tura {i}", "pl")
    # Last call saw only the two most recent prior turns.
    assert [u for u, _ in b.calls[3][1]] == ["tura 1", "tura 2"]


def test_history_survives_window_expiry_but_not_its_ttl(tmp_path):
    """Live 2026-08-31 08:53: an expletive aside hit an expired window and the
    old expiry-clears-history rule wiped the topic — the very next question
    lost its context. The thread now survives window expiry (a re-wake within
    the TTL continues the topic) and clears only when stale."""
    b = _HistBackend()
    a = Assistant(memory=MemoryStore(tmp_path / "memory.json"),
                  router=_OneBackendRouter(b), always_awake=False,
                  wake_window_s=20.0)
    a._notes_context = lambda text, lang: ""
    a.notify_wake(NOW)
    a.route("opowiedz mi coś o Jadźwingach", now=NOW)
    assert a._chat_history
    # A stray utterance three minutes later hits an expired window → asleep…
    aside = NOW + timedelta(minutes=3)
    assert a.route("oh nieważne", now=aside).action == "asleep"
    assert a._chat_history  # …but the thread SURVIVES
    # Re-wake shortly after → the follow-up carries the Jadźwingowie context.
    a.notify_wake(aside)
    a.route("a gdzie znajdowały się ich siedziby?", now=aside)
    assert b.calls[-1][1] and "Jadźwingach" in b.calls[-1][1][0][0]
    # …but a topic left alone past the TTL starts fresh.
    stale = aside + timedelta(minutes=15)
    a.notify_wake(stale)
    a.route("Dżesika, opowiedz o psach", now=stale)
    assert b.calls[-1][1] is None  # no history reached the LLM


def test_tool_commands_do_not_enter_history(tmp_path):
    b = _HistBackend()
    a = _assistant(tmp_path, b)
    a.route("która godzina", now=NOW)  # local clock handler, not the LLM
    assert a._chat_history == []


# ----- ASR follow-up capture --------------------------------------------------

from blazend.domains.voice_input.adapters.rpi5.asr.__main__ import (  # noqa: E402
    _capture_followup,
)

SR = 16_000


class _FakeRing:
    sample_rate = SR

    def __init__(self, speech_s: int, silence_s: int = 4) -> None:
        rng = np.random.default_rng(7)
        speech = (rng.normal(0, 3000, SR * speech_s)).astype(np.int16)
        silence = (rng.normal(0, 20, SR * silence_s)).astype(np.int16)
        self._data = np.concatenate([speech, silence]) if speech_s else silence
        self._pos = 0

    @property
    def write_pos(self) -> int:
        self._pos = min(self._pos + SR, len(self._data))
        return self._pos

    def read_range(self, begin: int, end: int):
        return self._data[begin:end]


class _FakeTranscriber:
    def transcribe(self, pcm, sample_rate):
        class _T:
            text = "a co z kotami perskimi?"
            language = "pl"
            confidence = 0.9
        return _T()


class _FakePub:
    def __init__(self) -> None:
        self.published: list = []

    async def publish(self, env) -> None:
        self.published.append(env)


def test_followup_publishes_asr_final(tmp_path):
    pub = _FakePub()
    asyncio.run(_capture_followup(
        pub, _FakeRing(speech_s=2), _FakeTranscriber(), None, 0.78,
        tmp_path / "clips", speech_rms=200.0, max_s=12.0, silence_s=1.2,
        lead_in_s=5.0))
    assert len(pub.published) == 1
    env = pub.published[0]
    assert env.topic == "asr.final"
    assert env.data["text"] == "a co z kotami perskimi?"


def test_followup_silence_publishes_followup_empty(tmp_path):
    pub = _FakePub()
    asyncio.run(_capture_followup(
        pub, _FakeRing(speech_s=0), _FakeTranscriber(), None, 0.78,
        tmp_path / "clips", speech_rms=200.0, max_s=12.0, silence_s=1.2,
        lead_in_s=2.0))
    assert len(pub.published) == 1
    env = pub.published[0]
    assert env.topic == "error" and env.data["code"] == "asr.followup_empty"


# ----- orchestrator follow-up arming ------------------------------------------

from blazend.domains.systems.adapters.rpi5.orchestrator.supervisor import (  # noqa: E402
    Orchestrator,
)


class _FollowupStub:
    def __init__(self, rt: Path) -> None:
        import types

        self._runtime_dir = rt
        self._radio = types.SimpleNamespace(playing=False)
        self._followup_until = 0.0
        self._awake_until = 0.0
        self._wake_window_s = 20.0
        self.beeped = 0

    async def _wait_speech_done(self) -> None:
        pass

    def _wake_chime_armed(self) -> bool:
        return True

    async def _play_wake_beep(self) -> None:
        self.beeped += 1


def test_arm_followup_chimes_and_touches_marker(tmp_path):
    stub = _FollowupStub(tmp_path)
    asyncio.run(Orchestrator._arm_followup(stub))
    assert stub.beeped == 1
    assert (tmp_path / "followup-capture").exists()
    assert stub._followup_until > 0.0


def test_arm_followup_skips_when_radio_grabbed_speaker(tmp_path):
    stub = _FollowupStub(tmp_path)
    stub._radio.playing = True
    asyncio.run(Orchestrator._arm_followup(stub))
    assert stub.beeped == 0
    assert not (tmp_path / "followup-capture").exists()


def test_first_turn_ambient_prose_after_false_wake_is_silent(tmp_path):
    """Live 2026-08-30 18:44: a false wake let overheard room conversation
    reach the LLM, which replied and armed a follow-up chain into a private
    talk. The FIRST turn after a wake with no wake token and ambient-looking
    prose gets silence; an established conversation is exempt."""
    b = _HistBackend()
    a = Assistant(memory=MemoryStore(tmp_path / "memory.json"),
                  router=_OneBackendRouter(b), always_awake=False)
    a._notes_context = lambda text, lang: ""
    a.notify_wake(NOW)
    babble = ("To tak jak z Iką, słucham, to jestem tak jak z Iką "
              "rozmawiam i mówię jej, że nie mają racji")
    r = a.route(babble, now=NOW)
    assert r.action == "asleep" and r.text == ""
    assert b.calls == []  # the LLM never saw it
    # Real conversation established → long follow-up answers still route.
    a.route("Dżesika, opowiedz mi o kotach", now=NOW)
    long_answer = ("a powiedz mi jeszcze proszę dokładnie jak wygląda "
                   "sprawa z kotami perskimi w polskich domach")
    r2 = a.route(long_answer, now=NOW)
    assert r2.action == "chat"


def test_reply_extends_window_from_real_clock_with_allowance(tmp_path):
    """Live 2026-08-31 08:42: the window counted from route ENTRY expired
    during a long LLM generation, so Jessica's own follow-up got the sleep
    notice. After a reply the window must survive speaking + the answer."""
    b = _HistBackend()
    a = Assistant(memory=MemoryStore(tmp_path / "memory.json"),
                  router=_OneBackendRouter(b), always_awake=False,
                  wake_window_s=20.0)
    a._notes_context = lambda text, lang: ""
    a.notify_wake(NOW)
    a.route("Dżesika, opowiedz o kotach", now=NOW)
    # The deadline sits wake_window+allowance past route entry — enough for
    # generation + speech + the follow-up capture.
    remaining = (a._awake_until - NOW).total_seconds()
    assert remaining > 60.0
    # A follow-up arriving a minute later (slow gen + long reply) still routes.
    late_followup = NOW + timedelta(seconds=75)
    assert a.route("a co z kotami perskimi?", now=late_followup).action == "chat"


def test_followup_hallucination_treated_as_empty(tmp_path):
    from blazend.domains.voice_input.adapters.rpi5.asr.__main__ import (
        _looks_hallucinated,
    )

    assert _looks_hallucinated("Kraków " * 19 + "Jedynka")
    assert not _looks_hallucinated("chcę zarejestrować w Polsce samochód z Niemiec")
    assert not _looks_hallucinated("tak tak")  # short affirmations untouched
