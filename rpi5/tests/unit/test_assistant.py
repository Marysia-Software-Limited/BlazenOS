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
from blazend.assistant.memory import MemoryStore
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
