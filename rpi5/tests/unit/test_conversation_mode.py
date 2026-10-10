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
from blazend.ipc import Envelope

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
    a.route("sprawdź opowiedz mi coś o Jadźwingach", now=NOW)
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
    a.route("Dżesika, sprawdź opowiedz o psach", now=stale)
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


def test_followup_low_confidence_closes_conversation(tmp_path):
    # Room babble whisper "understood" at conf 0.42-0.55 kept a wake-less
    # loop talking for 12 minutes (2026-08-31) — below min_conf the follow-up
    # must close the conversation, not feed the LLM.
    class _LowConf(_FakeTranscriber):
        def transcribe(self, pcm, sample_rate):
            t = super().transcribe(pcm, sample_rate)
            t.confidence = 0.5
            return t

    pub = _FakePub()
    asyncio.run(_capture_followup(
        pub, _FakeRing(speech_s=2), _LowConf(), None, 0.78,
        tmp_path / "clips", speech_rms=200.0, max_s=12.0, silence_s=1.2,
        lead_in_s=5.0, min_conf=0.6))
    assert len(pub.published) == 1
    env = pub.published[0]
    assert env.topic == "error" and env.data["code"] == "asr.followup_empty"


def test_followup_silence_publishes_followup_empty(tmp_path):
    pub = _FakePub()
    asyncio.run(_capture_followup(
        pub, _FakeRing(speech_s=0), _FakeTranscriber(), None, 0.78,
        tmp_path / "clips", speech_rms=200.0, max_s=12.0, silence_s=1.2,
        lead_in_s=2.0))
    assert len(pub.published) == 1
    env = pub.published[0]
    assert env.topic == "error" and env.data["code"] == "asr.followup_empty"


# ----- closed command list + "sprawdź" + hot follow-up (2026-09-08) -----------

from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.engine import (  # noqa: E402
    llm_awaits_reply,
)


def test_ai_query_command_opens_chat(tmp_path):
    b = _HistBackend()
    a = _assistant(tmp_path, b)
    r = a.route("sprawdź kim byli Jadźwingowie", now=NOW)
    assert r.action == "chat"
    assert b.calls[-1][0] == "kim byli Jadźwingowie"  # verb stripped
    r2 = a.route("zapytaj jaka będzie zima", now=NOW)
    assert r2.action == "chat"


def test_unmatched_speech_is_not_understood_not_chat(tmp_path):
    """Closed command list: random speech no longer falls through to the LLM."""
    b = _HistBackend()
    a = _assistant(tmp_path, b)
    r = a.route("kupiłem wczoraj nowe buty w galerii", now=NOW)
    assert r.action == "not_understood" and r.text == ""
    assert b.calls == []  # the LLM never saw it


def test_bare_ai_query_arms_one_shot_ask_window(tmp_path):
    b = _HistBackend()
    a = _assistant(tmp_path, b)
    r = a.route("sprawdź", now=NOW)
    assert r.action == "chat" and "sprawdzić" in r.text  # "Co mam sprawdzić?"
    assert b.calls == []
    # The NEXT utterance routes to the LLM prefix-free…
    r2 = a.route("ile ludzi mieszka w Krakowie", now=NOW + timedelta(seconds=10))
    assert r2.action == "chat" and b.calls[-1][0].startswith("ile ludzi")
    # …and the window is one-shot + time-bound.
    a2 = _assistant(tmp_path, _HistBackend())
    a2.route("sprawdź", now=NOW)
    r3 = a2.route("cokolwiek innego", now=NOW + timedelta(seconds=60))
    assert r3.action == "not_understood"


def test_llm_awaits_reply_detector():
    assert llm_awaits_reply("Bitwa odbyła się w 1410 roku. Czy chcesz wiedzieć więcej?")
    assert llm_awaits_reply("Mogę to rozwinąć — daj znać.")
    assert not llm_awaits_reply("Bitwa pod Grunwaldem odbyła się w 1410 roku.")
    assert not llm_awaits_reply("Do zobaczenia!")


def test_arm_followup_hot_touches_hot_marker(tmp_path):
    stub = _FollowupStub(tmp_path)
    asyncio.run(Orchestrator._arm_followup(stub, hot=True))
    assert (tmp_path / "followup-capture-hot").exists()
    assert not (tmp_path / "followup-capture").exists()


# ----- wake-less stop sentinel (2026-09-08) -----------------------------------

from blazend.domains.voice_input.adapters.rpi5.asr.__main__ import (  # noqa: E402
    _check_stop,
)


class _StopTranscriber:
    def __init__(self, text: str, conf: float = 0.9) -> None:
        self._text, self._conf = text, conf

    def transcribe(self, pcm, sample_rate):
        class _T:
            text = self._text
            language = "pl"
            confidence = self._conf
        return _T()


def _run_stopcheck(text: str, conf: float = 0.9) -> list:
    pub = _FakePub()
    asyncio.run(_check_stop(pub, _FakeRing(speech_s=2), _StopTranscriber(text, conf)))
    return pub.published


def test_stop_sentinel_routes_canonical_stop():
    out = _run_stopcheck("Stop!")
    assert len(out) == 1 and out[0].topic == "asr.final"
    assert out[0].data["text"] == "stop"
    out = _run_stopcheck("Dżesika, przestań!")
    assert len(out) == 1 and out[0].data["text"] == "stop"


def test_stop_sentinel_discards_everything_else():
    # Room talk / radio leak-through over a stream must NOT act or reply.
    assert _run_stopcheck("Natomiast przyjemnie przyspiesza i ma kierunkowskazy") == []
    assert _run_stopcheck("no i co ty na to") == []
    assert _run_stopcheck("Stop!", conf=0.3) == []  # unsure = discard
    assert _run_stopcheck("Kraków Kraków Kraków") == []  # hallucination guard


class _StopcheckStub:
    def __init__(self, rt: Path, playing: bool = True) -> None:
        import types

        self._runtime_dir = rt
        self._radio = types.SimpleNamespace(playing=playing)
        self._stopcheck_at = 0.0

    def _dialog_active(self) -> bool:
        return False


def _vad_end(ms: float):
    return Envelope(topic="vad.end", source="blazend-audio-in",
                    data={"duration_ms": ms})


def test_stop_sentinel_arms_only_during_activity(tmp_path):
    async def run() -> None:
        stub = _StopcheckStub(tmp_path, playing=True)
        Orchestrator._maybe_stopcheck(stub, _vad_end(600))
        assert (tmp_path / "stopcheck-capture").exists()
        (tmp_path / "stopcheck-capture").unlink()
        # Rate limit: a second utterance right behind does not re-arm.
        Orchestrator._maybe_stopcheck(stub, _vad_end(600))
        assert not (tmp_path / "stopcheck-capture").exists()
        # Idle (no stream, no speaking marker) never arms.
        idle = _StopcheckStub(tmp_path, playing=False)
        Orchestrator._maybe_stopcheck(idle, _vad_end(600))
        assert not (tmp_path / "stopcheck-capture").exists()
        # A click (too short) and babble (too long) never arm.
        stub2 = _StopcheckStub(tmp_path, playing=True)
        Orchestrator._maybe_stopcheck(stub2, _vad_end(80))
        Orchestrator._maybe_stopcheck(stub2, _vad_end(9000))
        assert not (tmp_path / "stopcheck-capture").exists()

    asyncio.run(run())


class _ActivityStub:
    def __init__(self, rt: Path, playing: bool) -> None:
        import types

        self._runtime_dir = rt
        self._radio = types.SimpleNamespace(playing=playing)
        self._activity_listen = rt / "activity-listen"


def test_activity_listen_marker_mirrors_sentinel_gate(tmp_path):
    """The marker that lets audio-in free-run its VAD must track the
    sentinel's own activity gate — without it no vad.end can ever arrive to
    arm the stop check (the 2026-10-10 unstoppable-radio regression)."""
    # Stream playing → marker up.
    Orchestrator._sync_activity_listen(_ActivityStub(tmp_path, playing=True))
    assert (tmp_path / "activity-listen").exists()
    # Idle → removed (also heals a stale marker after a crash mid-stream).
    Orchestrator._sync_activity_listen(_ActivityStub(tmp_path, playing=False))
    assert not (tmp_path / "activity-listen").exists()
    # A spoken reply (speaking marker) alone also holds it.
    (tmp_path / "speaking").touch()
    Orchestrator._sync_activity_listen(_ActivityStub(tmp_path, playing=False))
    assert (tmp_path / "activity-listen").exists()
    (tmp_path / "speaking").unlink()
    Orchestrator._sync_activity_listen(_ActivityStub(tmp_path, playing=False))
    assert not (tmp_path / "activity-listen").exists()


def test_stop_order_routes_even_asleep(tmp_path):
    """The engine must never sleep-gate a bare stop ("Śpię — powiedz Jessica"
    in reply to "stop" over a playing radio was the unstoppable-radio bug)."""
    b = _HistBackend()
    a = Assistant(memory=MemoryStore(tmp_path / "memory.json"),
                  router=_OneBackendRouter(b), always_awake=False)
    a._notes_context = lambda text, lang: ""
    assert not a.awake
    r = a.route("stop", now=NOW)
    assert r.action != "asleep"
    r2 = a.route("przestań", now=NOW + timedelta(seconds=1))
    assert r2.action != "asleep"
    # Control: ordinary speech is still sleep-gated.
    r3 = Assistant(memory=MemoryStore(tmp_path / "m2.json"),
                   router=_OneBackendRouter(_HistBackend()),
                   always_awake=False).route("która godzina", now=NOW)
    assert r3.action == "asleep"


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
    a.route("Dżesika, sprawdź opowiedz mi o kotach", now=NOW)
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
    a.route("Dżesika, sprawdź opowiedz o kotach", now=NOW)
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
    # Live 2026-08-31: a real "Dżesika, włącz trójkę" transcribed as the wake
    # word looping — the guard now also covers the main post-wake path.
    assert _looks_hallucinated("Dżesika, " * 8)
    assert _looks_hallucinated("Dżesika, Dwójka, " * 27)
    assert not _looks_hallucinated("chcę zarejestrować w Polsce samochód z Niemiec")
    assert not _looks_hallucinated("tak tak")  # short affirmations untouched
    assert not _looks_hallucinated("Dżesika, włącz radio Trójka proszę")
    # Stock whisper silence phrases (the 2026-08-31 wake-less loop fuel).
    assert _looks_hallucinated("Dziękuje za oglądanie.")
    assert _looks_hallucinated("Napisy stworzone przez społeczność Amara.org")
    assert not _looks_hallucinated("dziękuję, to wszystko")
    # A single word looping is a hallucination at ANY length ≥3 — "Kraków ×3"
    # (conf 0.47, silent room, 2026-09-08) started Radio Kraków via the
    # short-ask gate before this held.
    assert _looks_hallucinated("Kraków Kraków Kraków")
    assert not _looks_hallucinated("włącz Trójkę")
    assert not _looks_hallucinated("stop stop")  # two = an insistent human


# ----- negative reactions + accidental-catch gate (2026-09-21) -----------------

from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.engine import (  # noqa: E402
    is_negative_reaction,
)


def test_negative_reaction_detector_pl_en():
    for t in ("spierdalaj", "Zamknij się!", "daj spokój", "fuck off",
              "Oh, shut up", "odczep się wreszcie"):
        assert is_negative_reaction(t), t
    for t in ("włącz radio", "która godzina", "opowiedz o Krakowie",
              "zatrzymaj muzykę", "sprawdź pogodę"):
        assert not is_negative_reaction(t), t


def test_negative_reaction_goes_silent_never_to_the_llm(tmp_path):
    backend = _HistBackend()
    a = _assistant(tmp_path, backend)
    r = a.route("spierdalaj", now=NOW)
    assert r.action == "negative_reaction" and r.text == ""
    assert r.data["transcript"] == "spierdalaj"
    assert backend.calls == []            # the LLM never saw it
    assert not a.engages("spierdalaj")    # and no "Chwileczkę" promise either


def test_negative_reaction_ends_the_chat_thread(tmp_path):
    backend = _HistBackend()
    a = _assistant(tmp_path, backend)
    a.route("sprawdź jaka będzie zima", now=NOW)
    assert a._chat_history                # conversation open
    r = a.route("zamknij się", now=NOW + timedelta(seconds=5))
    assert r.action == "negative_reaction"
    assert a._chat_history == []          # thread closed, nothing to continue


class _ForeignSpeaker:
    def similarity(self, pcm):
        return 0.12


class _OwnerSpeaker:
    def similarity(self, pcm):
        return 0.87


def test_followup_foreign_voice_closes_conversation(tmp_path):
    """A confident utterance in a NON-owner voice inside the wake-less window
    is an accidental catch (TV/side talk) — close, don't feed the LLM."""
    pub = _FakePub()
    asyncio.run(_capture_followup(
        pub, _FakeRing(speech_s=2), _FakeTranscriber(), None, 0.78,
        tmp_path / "clips", speech_rms=200.0, max_s=12.0, silence_s=1.2,
        lead_in_s=5.0, speaker_id=_ForeignSpeaker(), speaker_thr=0.45))
    assert len(pub.published) == 1
    env = pub.published[0]
    assert env.topic == "error" and env.data["code"] == "asr.followup_empty"
    assert env.data["message"] == "foreign voice in follow-up"


def test_followup_owner_voice_still_flows(tmp_path):
    pub = _FakePub()
    asyncio.run(_capture_followup(
        pub, _FakeRing(speech_s=2), _FakeTranscriber(), None, 0.78,
        tmp_path / "clips", speech_rms=200.0, max_s=12.0, silence_s=1.2,
        lead_in_s=5.0, speaker_id=_OwnerSpeaker(), speaker_thr=0.45))
    env = pub.published[0]
    assert env.topic == "asr.final" and env.data["speaker_owner"] is True
