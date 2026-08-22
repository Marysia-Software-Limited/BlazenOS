"""Tier 0 — voice-memo dictation capture + the utterance-clip handshake.

`_capture_memo` is driven with a fake ring (speech then silence) and a fake
transcriber — asserts silence termination, wav persistence next to memory.json
and the `system.event kind=memo_recorded` payload. `claim_last_clip` is the
file handshake that lets "zapamiętaj, że …" keep its own recording without
widening the closed asr.final schema.
"""
from __future__ import annotations

import asyncio
import json
import wave
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from blazend.domains.context.adapters.rpi5.memory import MemoryStore
from blazend.domains.voice_input.adapters.rpi5.asr.__main__ import (
    _capture_memo,
    _save_clip,
    _write_wav,
)

SR = 16_000


class _FakeRing:
    """Feeds one second of PCM per write_pos poll: speech, then silence."""

    sample_rate = SR

    def __init__(self, speech_s: int = 2, silence_s: int = 4) -> None:
        rng = np.random.default_rng(7)
        speech = (rng.normal(0, 3000, SR * speech_s)).astype(np.int16)
        silence = (rng.normal(0, 20, SR * silence_s)).astype(np.int16)
        self._data = np.concatenate([speech, silence])
        self._pos = 0

    @property
    def write_pos(self) -> int:
        self._pos = min(self._pos + SR, len(self._data))  # +1 s per poll
        return self._pos

    def read_range(self, begin: int, end: int) -> np.ndarray:
        return self._data[begin:end]


class _FakeTranscriber:
    def transcribe(self, pcm: np.ndarray, sample_rate: int):  # noqa: ANN201 — mirrors Transcriber
        class _T:
            text = "kupić filtr do wody w sobotę"
            language = "pl"
            confidence = 0.9
        assert len(pcm) > 0 and sample_rate == SR
        return _T()


class _FakePub:
    def __init__(self) -> None:
        self.published: list = []

    async def publish(self, env) -> None:  # noqa: ANN001
        self.published.append(env)


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("BLAZEN_DATA_DIR", str(tmp_path))
    return tmp_path


def test_memo_capture_stops_on_silence_and_publishes(tmp_path):
    pub = _FakePub()
    asyncio.run(_capture_memo(
        pub, _FakeRing(), _FakeTranscriber(),
        speech_rms=200.0, max_s=60.0, silence_s=1.5, lead_in_s=8.0))
    assert len(pub.published) == 1
    env = pub.published[0]
    assert env.topic == "system.event" and env.data["kind"] == "memo_recorded"
    assert env.data["transcript"].startswith("kupić filtr")
    wav = Path(env.data["audio_path"])
    assert wav.exists() and wav.parent == tmp_path / "voice_notes"
    # Stopped on silence: ~2 s speech + ~1.5-2 s quiet, nowhere near max_s.
    assert 2.0 <= env.data["duration_s"] <= 5.0
    with wave.open(str(wav)) as w:
        assert w.getframerate() == SR and w.getnchannels() == 1


def test_memo_capture_aborts_when_nothing_said():
    pub = _FakePub()
    asyncio.run(_capture_memo(
        pub, _FakeRing(speech_s=0, silence_s=4), _FakeTranscriber(),
        speech_rms=200.0, max_s=60.0, silence_s=1.5, lead_in_s=2.0))
    assert len(pub.published) == 1
    env = pub.published[0]
    # Distinct code: the orchestrator speaks a memo-specific retry prompt.
    assert env.topic == "error" and env.data["code"] == "asr.memo_empty"


# -- the utterance-clip handshake ("zapamiętaj" keeps its audio) ---------------
def _drop_clip(tmp_path: Path, text: str) -> Path:
    clips = tmp_path / "clips"
    pcm = np.zeros(SR, dtype=np.int16)
    _save_clip(pcm, SR, text, clips)
    return clips


def test_claim_last_clip_moves_wav_on_text_match(tmp_path):
    _drop_clip(tmp_path, "Jessica, zapamiętaj, że kod do bramy to cztery siedem.")
    mem = MemoryStore(tmp_path / "memory.json")
    claimed = mem.claim_last_clip("kod do bramy to cztery siedem")
    assert claimed is not None
    path, duration = claimed
    assert Path(path).exists() and Path(path).parent == tmp_path / "voice_notes"
    assert duration == pytest.approx(1.0, abs=0.2)
    assert not (tmp_path / "clips" / "last.json").exists()  # single-use claim


def test_claim_last_clip_refuses_mismatched_text(tmp_path):
    clips = _drop_clip(tmp_path, "Jessica, włącz trójkę.")
    mem = MemoryStore(tmp_path / "memory.json")
    assert mem.claim_last_clip("kod do bramy") is None
    assert (clips / "last.json").exists()  # untouched


def test_claim_last_clip_refuses_stale_clip(tmp_path):
    clips = _drop_clip(tmp_path, "Jessica, zapamiętaj, że test.")
    marker = json.loads((clips / "last.json").read_text(encoding="utf-8"))
    marker["ts"] = datetime(2020, 1, 1, tzinfo=UTC).isoformat()
    (clips / "last.json").write_text(json.dumps(marker), encoding="utf-8")
    assert MemoryStore(tmp_path / "memory.json").claim_last_clip("test") is None


def test_save_clip_prunes_and_write_wav_roundtrips(tmp_path):
    clips = tmp_path / "clips"
    for i in range(55):
        _save_clip(np.zeros(160, dtype=np.int16), SR, f"utterance {i}", clips)
    assert len(list(clips.glob("clip-*.wav"))) <= 50  # rolling cap
    p = tmp_path / "one.wav"
    _write_wav(np.ones(320, dtype=np.int16), SR, p)
    with wave.open(str(p)) as w:
        assert w.getnframes() == 320


# -- recall UX: memo playback queue + explicit memory search -------------------
def _store_with_memos(tmp_path: Path) -> MemoryStore:
    mem = MemoryStore(tmp_path / "memory.json")
    for i, text in enumerate(["kod do bramy cztery siedem", "kupić filtr do wody"]):
        wav = tmp_path / "voice_notes" / f"m{i}.wav"
        wav.parent.mkdir(parents=True, exist_ok=True)
        _write_wav(np.zeros(SR // 2, dtype=np.int16), SR, wav)
        mem.add_voice_note(wav, now=datetime(2026, 7, 29, tzinfo=UTC), transcript=text)
    mem.add_note("hasło do wifi: piesek", now=datetime(2026, 7, 29, tzinfo=UTC))
    return mem


def test_play_memos_returns_playlist_queue(tmp_path):
    from blazend.domains.ai_orchestrator.adapters.rpi5.tools import Tools
    t = Tools(memory=_store_with_memos(tmp_path))
    r = t.play_memos("pl")
    assert r.action == "music_play" and r.payload["is_playlist"] is True
    assert len(r.payload["chapters"]) == 2
    assert "2 nagrania" in r.text


def test_search_memory_lexical_fallback_offers_replay(tmp_path):
    from blazend.domains.ai_orchestrator.adapters.rpi5.tools import Tools
    t = Tools(memory=_store_with_memos(tmp_path))
    t._embedder_cache = None  # force the lexical (CPU-contract) fallback
    r = t.search_memory("kod do bramy", "pl")
    assert "kod do bramy cztery siedem" in r.text
    assert "odtwórz nagranie" in r.text  # voice hit → replay offer
    follow = t.play_found_memo("pl")
    assert follow.action == "music_play" and len(follow.payload["chapters"]) == 1


def test_search_memory_finds_text_notes_without_replay_offer(tmp_path):
    from blazend.domains.ai_orchestrator.adapters.rpi5.tools import Tools
    t = Tools(memory=_store_with_memos(tmp_path))
    t._embedder_cache = None
    r = t.search_memory("hasło do wifi", "pl")
    assert "piesek" in r.text and "nagranie" not in r.text


# -- memory management by voice (2026-08-04) -----------------------------------
def test_delete_last_memory_removes_newest_and_trashes_wav(tmp_path):
    mem = _store_with_memos(tmp_path)  # 2 memos then 1 note (note is newest? all same ts)
    # Make ordering explicit: add a newest voice memo.
    wav = tmp_path / "voice_notes" / "newest.wav"
    _write_wav(np.zeros(SR // 4, dtype=np.int16), SR, wav)
    vn = mem.add_voice_note(wav, now=datetime(2026, 8, 4, tzinfo=UTC), transcript="najnowsze nagranie")
    mem.set_note_embedding(vn.id, [1.0, 0.0, 0.0], model="m")
    item = mem.delete_last_memory()
    assert item is not None and item.id == vn.id and item.kind == "voice"
    assert not wav.exists()  # moved away…
    assert (tmp_path / "trash" / "newest.wav").exists()  # …into trash, not deleted
    assert vn.id not in mem._load_embeddings()["vectors"]  # vector gone
    assert all(v.id != vn.id for v in mem.voice_notes())


def test_delete_last_memory_on_empty_store(tmp_path):
    assert MemoryStore(tmp_path / "m.json").delete_last_memory() is None


def test_tools_memory_stats_and_delete(tmp_path):
    from blazend.domains.ai_orchestrator.adapters.rpi5.tools import Tools
    t = Tools(memory=_store_with_memos(tmp_path))
    r = t.memory_stats("pl")
    assert "1 notatkę" in r.text and "2 nagrania" in r.text
    d = t.delete_last_memory("pl")
    assert d.text.startswith("Usunęłam: ")
    r2 = t.memory_stats("pl")
    assert r2.payload["notes"] + r2.payload["voice_notes"] == 2
    # Empty store answers gracefully.
    t2 = Tools(memory=MemoryStore(tmp_path / "empty.json"))
    assert "żadnych wspomnień" in t2.memory_stats("pl").text
    assert "Nie mam czego" in t2.delete_last_memory("pl").text


def test_voice_note_wav_falls_back_to_synced_mirror(tmp_path):
    mem = MemoryStore(tmp_path / "memory.json")
    v = mem.add_voice_note("/on/another/node/memo.wav", now=datetime(2026, 8, 4, tzinfo=UTC),
                           transcript="zdalne nagranie")
    assert mem.voice_note_wav(v.id, v.audio_path) is None  # nothing local yet
    mirror = tmp_path / "voice_notes" / "synced" / f"{v.id}.wav"
    mirror.parent.mkdir(parents=True, exist_ok=True)
    _write_wav(np.zeros(160, dtype=np.int16), SR, mirror)
    assert mem.voice_note_wav(v.id, v.audio_path) == mirror  # fabric mirror plays


# -- memo dictation dialog: title → content (2026-08-04) -----------------------
class _DialogStub:
    """Just enough Orchestrator surface for the unbound _on_memo_recorded."""

    def __init__(self) -> None:
        self.spoken: list[str] = []
        self.windows = 0
        self._memo_stage: str | None = None
        self._memo_title = ""
        self._memo_lang = "pl"
        self._voice_train_round = 0  # voice-training dialog off (2026-08-22)
        self._voice_train_retries = 0

    async def _speak(self, text: str, lang: str = "pl") -> None:
        self.spoken.append(text)

    async def _wait_speech_done(self) -> None:
        pass

    def _open_memo_window(self) -> None:
        self.windows += 1


def test_memo_dialog_title_then_content(tmp_path):
    from blazend.domains.systems.adapters.rpi5.orchestrator.supervisor import Orchestrator

    stub = _DialogStub()
    stub._memo_stage = "title"
    title_wav = tmp_path / "voice_notes" / "memo-title.wav"
    title_wav.parent.mkdir(parents=True, exist_ok=True)
    _write_wav(np.zeros(160, dtype=np.int16), SR, title_wav)

    # Step 1: the title capture — wav discarded, content prompt, window reopened.
    asyncio.run(Orchestrator._on_memo_recorded(stub, {
        "audio_path": str(title_wav), "transcript": "Zakupy na sobotę.",
        "language": "pl", "duration_s": 1.0}))
    assert stub._memo_stage == "content" and stub._memo_title == "Zakupy na sobotę"
    assert not title_wav.exists()  # a title is text-only
    assert stub.spoken == ["Podyktuj treść."] and stub.windows == 1

    # Step 2: the content capture — memo stored under the title, confirmed.
    content_wav = tmp_path / "voice_notes" / "memo-content.wav"
    _write_wav(np.zeros(320, dtype=np.int16), SR, content_wav)
    asyncio.run(Orchestrator._on_memo_recorded(stub, {
        "audio_path": str(content_wav), "transcript": "kupić filtr do wody i mleko",
        "language": "pl", "duration_s": 2.0}))
    assert stub._memo_stage is None
    assert stub.spoken[-1] == "Nagrałam notatkę: Zakupy na sobotę."
    saved = MemoryStore(tmp_path / "memory.json").voice_notes()
    assert len(saved) == 1
    assert saved[0].title == "Zakupy na sobotę"
    assert saved[0].transcript == "kupić filtr do wody i mleko"
    assert saved[0].audio_path == str(content_wav) and content_wav.exists()


def test_memo_dialog_untitled_when_title_capture_empty(tmp_path):
    from blazend.domains.systems.adapters.rpi5.orchestrator.supervisor import Orchestrator

    stub = _DialogStub()
    stub._memo_stage = "title"
    asyncio.run(Orchestrator._on_memo_recorded(stub, {
        "audio_path": str(tmp_path / "none.wav"), "transcript": "",
        "language": "pl", "duration_s": 0.5}))
    assert stub._memo_stage == "content" and stub._memo_title == ""
    assert stub.spoken == ["Nie usłyszałam tytułu — podyktuj samą treść."]


def test_memo_queue_envelope_reaches_the_player():
    """First live "odtwórz notatki" (2026-08-09): tools built a correct queue,
    but the reply-envelope builder accepted only music.*/audiobook.* tool
    names — a context.play_memos payload was DROPPED, so Jessica announced
    "Odtwarzam 2 nagrania" and then played nothing. Third payload-drop at
    this seam (2026-07-27 lesson: test the dispatcher→envelope seam)."""
    from types import SimpleNamespace

    from blazend.domains.ai_orchestrator.adapters.rpi5.dispatch import DispatchResult
    from blazend.domains.systems.adapters.rpi5.orchestrator.supervisor import Orchestrator
    from blazend.events import Envelope

    wavs = ["/data/voice_notes/memo-a.wav", "/data/voice_notes/memo-b.wav"]

    class _Disp:
        def dispatch(self, intent: str, params: dict, lang: str) -> DispatchResult:
            payload = {"path": wavs[0], "name": "notatki głosowe",
                       "is_playlist": True, "chapters": list(wavs), "chapter": 0,
                       "labels": ["Informacje ogóliste", "Bandera Power"]}
            return DispatchResult("Odtwarzam 2 nagrania.", lang, "tool",
                                  data={"tool": "context.play_memos", "ok": True, **payload})

    stub = SimpleNamespace(_dispatcher=_Disp(), _radio=SimpleNamespace(playing=False),
                           _music_enabled=True, _book=None, _last_book=None,
                           _last_source="", _last_source_name="")
    env = Envelope(topic="nlu.intent", source="test",
                   data={"intent": "voice_memo_play", "params": {}, "language": "pl"})
    reply = Orchestrator._dispatch_intent(stub, env)
    assert reply is not None and reply.data["action"] == "music_play"
    payload = reply.data["payload"]
    assert payload["is_playlist"] is True and payload["chapters"] == wavs
    assert payload["labels"] == ["Informacje ogóliste", "Bandera Power"]


# -- voice training ("naucz się mojego głosu", 2026-08-22) --------------------
def test_voice_training_rounds_collect_samples_then_thank(tmp_path):
    """Five captures land as wavs in the training dir; rounds are narrated and
    the dialog thanks the owner at the end (blind-first)."""
    from blazend.domains.systems.adapters.rpi5.orchestrator.supervisor import Orchestrator

    stub = _DialogStub()
    stub._VOICE_TRAIN_DIR = tmp_path / "voice-training"
    stub._VOICE_TRAIN_ROUNDS = Orchestrator._VOICE_TRAIN_ROUNDS
    stub._on_voice_sample = Orchestrator._on_voice_sample.__get__(stub)
    stub._voice_train_round = 1

    async def run() -> None:
        for n in range(1, 6):
            clip = tmp_path / f"clip-{n}.wav"
            clip.write_bytes(b"RIFFxxxx")
            await Orchestrator._on_memo_recorded(
                stub, {"audio_path": str(clip), "transcript": "dżesika", "language": "pl"})

    asyncio.run(run())
    assert len(list((tmp_path / "voice-training").glob("wake-*.wav"))) == 5
    assert stub._voice_train_round == 0                       # dialog finished
    assert stub.windows == 4                                  # reopened between rounds
    assert any("Dziękuję" in s for s in stub.spoken)          # thanked at the end
    assert any("jeszcze cztery razy" in s for s in stub.spoken)


def test_voice_training_does_not_disturb_memo_dialog(tmp_path):
    """With training off, memo captures still walk the title/content dialog."""
    from blazend.domains.systems.adapters.rpi5.orchestrator.supervisor import Orchestrator

    stub = _DialogStub()
    stub._memo_stage = "title"
    wav = tmp_path / "t.wav"
    wav.write_bytes(b"RIFFxxxx")
    asyncio.run(Orchestrator._on_memo_recorded(
        stub, {"audio_path": str(wav), "transcript": "lista zakupów", "language": "pl"}))
    assert stub._memo_stage == "content"
