"""Tier 0 — the assistant prototype (offline, deterministic).

Covers name/wake detection, the PL+EN time parser, the persistent
notes/reminders store, and engine routing (remember / remind / recall /
news / chat) with a fake Gemini transport and an injected clock.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from blazend.assistant import wake
from blazend.assistant.engine import Assistant, detect_lang
from blazend.assistant.gemini import GeminiClient
from blazend.assistant.localllm import LocalLlm
from blazend.assistant.memory import MemoryStore
from blazend.assistant.openai import OpenAiClient
from blazend.assistant.timeparse import parse_when

NOW = datetime(2026, 6, 12, 14, 0, 0)


# --- wake / name -----------------------------------------------------------
def test_wake_detection_pl_en():
    assert wake.is_wake("Hej Jessico, jaka godzina?")
    assert wake.is_wake("jessica what time is it")
    assert wake.is_wake("ok Jess")
    assert not wake.is_wake("jaka jest pogoda")


def test_strip_wake():
    assert wake.strip_wake("Hej Jessico, jaka godzina?") == "jaka godzina?"
    assert wake.strip_wake("Jessica what time is it") == "what time is it"
    assert wake.strip_wake("Jessica") == "Jessica"  # bare name, nothing to route


# --- time parsing ----------------------------------------------------------
def test_timeparse_relative():
    assert parse_when("za 10 minut", NOW) == NOW + timedelta(minutes=10)
    assert parse_when("in 2 hours", NOW) == NOW + timedelta(hours=2)
    assert parse_when("za godzinę", NOW) == NOW + timedelta(hours=1)
    assert parse_when("za 5 sekund", NOW) == NOW + timedelta(seconds=5)


def test_timeparse_clock_and_tomorrow():
    assert parse_when("o 15:30", NOW) == NOW.replace(hour=15, minute=30)
    # 09:00 is in the past relative to 14:00 → rolls to tomorrow.
    assert parse_when("o 9", NOW) == NOW.replace(hour=9, minute=0) + timedelta(days=1)
    assert parse_when("jutro o 8:00", NOW) == NOW.replace(hour=8, minute=0) + timedelta(days=1)
    assert parse_when("at 3pm", NOW) == NOW.replace(hour=15, minute=0)
    assert parse_when("kompletny bełkot", NOW) is None


# --- memory store ----------------------------------------------------------
def test_memory_notes_and_reminders(tmp_path):
    store = MemoryStore(tmp_path / "mem.json")
    store.add_note("kod do bramy to 4729", now=NOW)
    assert [n.text for n in store.recall("kod")] == ["kod do bramy to 4729"]
    assert store.recall("nieistnieje") == []

    store.add_reminder("zadzwoń do mamy", due=NOW + timedelta(seconds=5), now=NOW)
    assert store.due(NOW) == []  # not yet
    fired = store.due(NOW + timedelta(seconds=6))
    assert [r.text for r in fired] == ["zadzwoń do mamy"]
    assert store.due(NOW + timedelta(seconds=7)) == []  # fires once

    # persisted across reopen
    assert len(MemoryStore(tmp_path / "mem.json").notes()) == 1


def test_detect_lang():
    assert detect_lang("jaka jest godzina") == "pl"
    assert detect_lang("what is the time") == "en"


# --- engine routing --------------------------------------------------------
def _fake_transport(reply_text):
    def transport(_url, _body):
        return {"candidates": [{"content": {"parts": [{"text": reply_text}]}}]}
    return transport


def _assistant(tmp_path, *, gemini_reply=None):
    gem = GeminiClient(
        api_key="test-key" if gemini_reply is not None else "",
        transport=_fake_transport(gemini_reply or ""),
    )
    return Assistant(memory=MemoryStore(tmp_path / "mem.json"), gemini=gem)


def test_engine_requires_wake(tmp_path):
    a = _assistant(tmp_path)
    assert a.route("jaka godzina", now=NOW).action == "asleep"
    assert a.route("Jessica", now=NOW).action == "wake"
    # now awake → follow-ups route without the name
    assert a.route("zapamiętaj że lubię kawę", now=NOW).action == "note"


def test_engine_remember_and_recall(tmp_path):
    a = _assistant(tmp_path)
    a.awake = True
    r = a.route("zapamiętaj, że hasło do wifi to lato2026", now=NOW)
    assert r.action == "note" and "lato2026" in r.text
    rec = a.route("co pamiętasz?", now=NOW)
    assert rec.action == "recall" and "lato2026" in rec.text


def test_engine_reminder_fires(tmp_path):
    a = _assistant(tmp_path)
    a.awake = True
    r = a.route("przypomnij mi żeby wyjąć pranie za 5 sekund", now=NOW)
    assert r.action == "reminder" and "pranie" in r.text
    assert a.due_reminders(NOW) == []
    fired = a.due_reminders(NOW + timedelta(seconds=6))
    assert len(fired) == 1 and "pranie" in fired[0].text


def test_engine_news_needs_key(tmp_path):
    a = _assistant(tmp_path)  # no key
    a.awake = True
    r = a.route("co w wiadomościach?", now=NOW)
    assert r.action == "news" and r.data.get("needs_key")


def test_engine_news_grounded_with_key(tmp_path):
    a = _assistant(tmp_path, gemini_reply="Dziś główna wiadomość to...")
    a.awake = True
    r = a.route("Jessica, sprawdź wiadomości", now=NOW)
    assert r.action == "news" and r.text.startswith("Dziś główna wiadomość")


def test_engine_chat_with_key(tmp_path):
    a = _assistant(tmp_path, gemini_reply="Mam się dobrze, dziękuję!")
    a.awake = True
    r = a.route("jak się masz?", now=NOW)
    assert r.action == "chat" and "dziękuję" in r.text


# --- local LLM (first) + OpenAI (second layer) -----------------------------
class _FakeLlm:
    """LlmBackend Protocol stand-in for the on-device model."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.last_system: str | None = None

    def generate(self, *, system: str, user: str) -> str:
        self.last_system = system
        return self.reply


def _openai(reply):
    def transport(_url, _headers, _body):
        return {"choices": [{"message": {"content": reply}}]}
    return OpenAiClient(api_key="test-key", transport=transport)


def test_engine_chat_prefers_local_llm(tmp_path):
    a = Assistant(
        memory=MemoryStore(tmp_path / "m.json"),
        gemini=GeminiClient(api_key=""),
        llm=LocalLlm(backend=_FakeLlm("Mam się świetnie.")),
        openai=_openai("z OpenAI"),
    )
    a.awake = True
    r = a.route("jak się masz?", now=NOW)
    assert r.action == "chat" and r.data.get("engine") == "local"
    assert "świetnie" in r.text


def test_engine_chat_falls_back_to_openai(tmp_path):
    a = Assistant(
        memory=MemoryStore(tmp_path / "m.json"),
        gemini=GeminiClient(api_key=""),
        llm=None,
        openai=_openai("Cześć z OpenAI."),
    )
    a.awake = True
    r = a.route("opowiedz dowcip", now=NOW)
    assert r.action == "chat" and r.data.get("engine") == "openai"
    assert "OpenAI" in r.text


def test_engine_chat_canned_when_nothing(tmp_path):
    a = _assistant(tmp_path)  # no key, no llm, no openai
    a.awake = True
    r = a.route("jak się masz?", now=NOW)
    assert r.action == "chat" and r.data.get("needs_key")


def test_engine_news_still_needs_gemini_with_local_llm(tmp_path):
    # The local LLM / OpenAI must NOT satisfy a web-grounded news lookup.
    a = Assistant(
        memory=MemoryStore(tmp_path / "m.json"),
        gemini=GeminiClient(api_key=""),
        llm=LocalLlm(backend=_FakeLlm("local")),
        openai=_openai("openai"),
    )
    a.awake = True
    r = a.route("co w wiadomościach?", now=NOW)
    assert r.action == "news" and r.data.get("needs_key")


# --- user's name (S2) ------------------------------------------------------
def test_engine_set_and_get_name_pl(tmp_path):
    a = _assistant(tmp_path)
    a.awake = True
    r = a.route("mam na imię Paweł", now=NOW)
    assert r.action == "profile" and r.data.get("name") == "Paweł" and "Paweł" in r.text
    g = a.route("jak mam na imię?", now=NOW)
    assert g.action == "profile" and "Paweł" in g.text


def test_engine_set_name_en_persists_across_instances(tmp_path):
    path = tmp_path / "mem.json"
    a = Assistant(memory=MemoryStore(path), gemini=GeminiClient(api_key=""))
    a.awake = True
    a.route("my name is Alex", now=NOW)
    a2 = Assistant(memory=MemoryStore(path), gemini=GeminiClient(api_key=""))
    a2.awake = True
    g = a2.route("what's my name?", now=NOW)
    assert g.action == "profile" and "Alex" in g.text


def test_engine_get_name_unknown(tmp_path):
    a = _assistant(tmp_path)
    a.awake = True
    r = a.route("jak mam na imię?", now=NOW)
    assert r.action == "profile" and r.data.get("name") is None


def test_engine_greets_by_name_on_wake(tmp_path):
    a = _assistant(tmp_path)
    a.awake = True
    a.route("mów do mnie Ola", now=NOW)
    r = a.route("Jessica", now=NOW)
    assert r.action == "wake" and "Ola" in r.text


def test_engine_chat_injects_name_into_system_prompt(tmp_path):
    fake = _FakeLlm("ok")
    a = Assistant(
        memory=MemoryStore(tmp_path / "m.json"),
        gemini=GeminiClient(api_key=""),
        llm=LocalLlm(backend=fake),
    )
    a.awake = True
    a.route("mam na imię Zofia", now=NOW)
    a.route("opowiedz coś o kawie", now=NOW)
    assert "Zofia" in (fake.last_system or "")


# --- reminders / alarms / events (S4) --------------------------------------
def test_engine_reminder_task_text_is_clean(tmp_path):
    a = _assistant(tmp_path)
    a.awake = True
    r = a.route("Hej Jessico, przypomnij mi za godzinę, że muszę otworzyć pokój", now=NOW)
    assert r.action == "reminder"
    assert r.data["text"] == "muszę otworzyć pokój"  # no stray "Hej"/commas
    assert "," not in r.text.split(":")[1]  # task half has no orphan commas


def test_engine_alarm_vocabulary(tmp_path):
    a = _assistant(tmp_path)
    a.awake = True
    r = a.route("ustaw alarm za 10 minut", now=NOW)
    assert r.action == "reminder" and r.data["category"] == "alarm"
    assert "udzik" in r.text or "larm" in r.text  # "budzik"/"alarm" wording


def test_engine_event_vocabulary(tmp_path):
    a = _assistant(tmp_path)
    a.awake = True
    r = a.route("dodaj wydarzenie spotkanie z Anną o 15:00", now=NOW)
    assert r.action == "reminder" and r.data["category"] == "event"
    assert "spotkanie z Anną" in r.data["text"]


def test_engine_alarm_vocabulary_en(tmp_path):
    a = _assistant(tmp_path)
    a.awake = True
    r = a.route("set an alarm in 10 minutes", now=NOW)
    assert r.action == "reminder" and r.data["category"] == "alarm"


def test_engine_recall_reminders_broadened(tmp_path):
    a = _assistant(tmp_path)
    a.awake = True
    a.route("ustaw alarm za 10 minut", now=NOW)
    r = a.route("co mam zaplanowane", now=NOW)
    assert r.action == "recall" and "budzik" in r.text  # the broadened phrase lists it


def test_engine_due_reminder_spoken_without_emoji(tmp_path):
    a = _assistant(tmp_path)
    a.awake = True
    a.route("przypomnij mi za 5 sekund, że pranie", now=NOW)
    fired = a.due_reminders(NOW + timedelta(seconds=6))
    assert len(fired) == 1
    assert "⏰" not in fired[0].text and "pranie" in fired[0].text
    assert fired[0].text.startswith("Przypominam")


# --- voice notes (S3) ------------------------------------------------------
def test_memory_voice_notes_persist(tmp_path):
    p = tmp_path / "m.json"
    MemoryStore(p).add_voice_note(tmp_path / "x.wav", now=NOW, duration_s=3.5, transcript="hej")
    vns = MemoryStore(p).voice_notes()
    assert len(vns) == 1 and vns[0].duration_s == 3.5 and vns[0].audio_path.endswith("x.wav")


def test_engine_voice_note_record_intent(tmp_path):
    a = _assistant(tmp_path)
    a.awake = True
    assert a.route("nagraj notatkę głosową", now=NOW).action == "voice_note_record"
    assert a.route("record a voice note", now=NOW).action == "voice_note_record"


def test_engine_voice_note_play_empty(tmp_path):
    a = _assistant(tmp_path)
    a.awake = True
    r = a.route("odtwórz notatki", now=NOW)
    assert r.action == "voice_note_play" and r.data["paths"] == []


def test_engine_voice_note_play_lists_stored(tmp_path):
    mem = MemoryStore(tmp_path / "m.json")
    mem.add_voice_note(tmp_path / "a.wav", now=NOW, duration_s=2.0)
    a = Assistant(memory=mem, gemini=GeminiClient(api_key=""), always_awake=True)
    r = a.route("play my voice notes", now=NOW)
    assert r.action == "voice_note_play" and len(r.data["paths"]) == 1
