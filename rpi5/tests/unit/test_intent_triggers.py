"""Regression guard for over-broad intent triggers (2026-07-10).

A false wake let ambient room speech reach the NLU, and bare-keyword track-nav
triggers (`\\b(poprzedni\\w*|…)\\b.*`) matched a keyword buried in an unrelated
sentence — so "…z poprzedniej rodziny…" fired `music_prev` and started music.
The triggers are now anchored to the whole utterance; assert the ambient sentences
no longer match while real commands still do.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
INTENTS = REPO / "configs" / "intents" / "system.yaml"


def _triggers(name: str, lang: str = "pl") -> list[re.Pattern[str]]:
    data = yaml.safe_load(INTENTS.read_text(encoding="utf-8"))
    for intent in data["intents"]:
        if intent.get("name") == name:
            return [re.compile(p, re.IGNORECASE) for p in intent["triggers"][lang]]
    raise AssertionError(f"intent {name!r} not found")


def _matches(name: str, text: str, lang: str = "pl") -> bool:
    return any(p.search(text) for p in _triggers(name, lang))


# -- the actual false positive that started music from room chatter -----------
def test_ambient_sentence_does_not_fire_music_prev():
    assert not _matches("music_prev", "A wtedy z poprzedniej rodziny też są zaproszeni?")


def test_ambient_sentences_do_not_fire_music_next():
    for s in ("Następnym razem zrobię to inaczej, obiecuję.",
              "No i tak dalej, dalej nie wiem co powiedzieć.",
              "To był kolejny nudny dzień w pracy."):
        assert not _matches("music_next", s), s


# -- real commands must still work --------------------------------------------
@pytest.mark.parametrize("cmd", ["poprzedni", "poprzedni utwór", "cofnij", "wstecz",
                                 "Jessica, poprzedni", "dżesiko poprzedni utwór"])
def test_real_prev_commands_match(cmd):
    assert _matches("music_prev", cmd)


@pytest.mark.parametrize("cmd", ["następny", "następny utwór", "dalej", "pomiń",
                                 "coś innego", "zagraj coś innego", "kolejny kawałek",
                                 "Jessica, następny", "dżesika następny utwór"])
def test_real_next_commands_match(cmd):
    assert _matches("music_next", cmd)


@pytest.mark.parametrize("cmd,ok", [("previous", True), ("next", True), ("skip", True),
                                    ("go back", True), ("the previous owner sold it", False)])
def test_english_next_prev(cmd, ok):
    assert (_matches("music_prev", cmd, "en") or _matches("music_next", cmd, "en")) is ok


# -- rain forecast owns rain questions; weather_query keeps general weather ----
def _named(name: str, text: str, group: str, lang: str = "pl") -> str | None:
    for p in _triggers(name, lang):
        m = p.search(text)
        if m and group in (m.groupdict() or {}):
            return m.group(group)
    return None


@pytest.mark.parametrize("q", ["czy będzie padać", "czy będzie padać?", "kiedy będzie padać",
                               "będzie deszcz", "opady dzisiaj", "czy wziąć parasol",
                               "czy potrzebuję parasola"])
def test_rain_questions_fire_rain_forecast(q):
    assert _matches("rain_forecast", q), q


@pytest.mark.parametrize("q", ["will it rain", "is it going to rain", "when will it rain",
                               "do I need an umbrella", "is it raining"])
def test_english_rain_questions_fire_rain_forecast(q):
    assert _matches("rain_forecast", q, "en"), q


@pytest.mark.parametrize("q", ["czy jutro będzie padać", "czy będzie padać jutro",
                               "jutro deszcz"])
def test_rain_tomorrow_is_captured(q):
    assert _named("rain_forecast", q, "when") == "jutro", q


def test_rain_place_is_captured():
    assert _named("rain_forecast", "czy będzie padać w Gdańsku", "place") == "Gdańsku"
    assert _named("rain_forecast", "will it rain in London", "place", "en") == "London"


def test_ambient_listopadzie_does_not_fire_rain():
    # Live false positive 2026-07-13: a false wake transcribed TV speech and
    # "pad\w*" matched inside "listoPADzie" → an unprompted rain forecast.
    # The stems are now \b-anchored; mid-word hits must not match.
    for s in ("czy przeciwnika. Zasadę tę zastosowano w listopadzie 1918 roku",
              "spotkamy się w listopadzie w Warszawie",
              "wypadek na autostradzie w Gdańsku"):
        assert not _matches("rain_forecast", s), s


def test_general_weather_does_not_hijack_to_rain():
    # "jaka pogoda" / temperature go to weather_query, not rain_forecast.
    for q in ("jaka jest pogoda", "jaka pogoda w Krakowie", "jaka temperatura"):
        assert _matches("weather_query", q), q
        assert not _matches("rain_forecast", q), q


# -- news: "jakie wieści?" fell through to LLM chat (2026-07-13) --------------
@pytest.mark.parametrize("q", [
    "Jessica? Jakie wieści?",            # the exact live transcript that missed
    "jakie są wieści",
    "co w wiadomościach",
    "co słychać",
    "co na świecie",
    "aktualności",
])
def test_news_questions_fire_news_brief(q):
    assert _matches("news_brief", q)


@pytest.mark.parametrize("q", ["what's the news", "any headlines", "what's happening"])
def test_english_news_questions_fire_news_brief(q):
    assert _matches("news_brief", q, lang="en")


# -- radio stop: whisper drops the "s" of "stop" over a loud radio (2026-07-21)
@pytest.mark.parametrize("cmd", [
    "Jessica, stop.",       # clean hearing — worked all along
    "Jessica, top!",        # the live mis-hearings that fell through to chat
    "Jessica. Top.",
    "Wszystkie. Top.",
])
def test_stop_survives_s_drop(cmd):
    assert _matches("radio_stop", cmd)


@pytest.mark.parametrize("cmd", ["stop", "top", "stop the music"])
def test_english_stop_survives_s_drop(cmd):
    assert _matches("radio_stop", cmd, lang="en")


def test_laptop_does_not_stop_radio():
    # \b keeps the s?top stem from firing mid-word.
    assert not _matches("radio_stop", "podaj mi laptopa z biurka")
    assert not _matches("radio_stop", "hand me the laptop", lang="en")


# -- track nav: whisper's trailing punctuation broke the ^…$ anchors (2026-07-27)
@pytest.mark.parametrize("cmd", [
    "Jessica, następny.",   # the live transcript that fell through to LLM chat
    "Jessica? Następny.",   # "?" after the wake word broke the [\s,]+ prefix
    "Jessica, następny!",
    "następny.",
])
def test_next_survives_whisper_punctuation(cmd):
    assert _matches("music_next", cmd)


@pytest.mark.parametrize("cmd", ["Jessica, poprzedni.", "Jessica? Poprzedni.", "poprzedni."])
def test_prev_survives_whisper_punctuation(cmd):
    assert _matches("music_prev", cmd)


# -- shuffle + now-playing (2026-07-27) ----------------------------------------
@pytest.mark.parametrize("cmd", [
    "Jessica, tasuj.", "Jessica, przetasuj!", "przetasuj", "pomieszaj utwory",
    "Jessica? Tasuj.",
])
def test_shuffle_commands_match(cmd):
    assert _matches("music_shuffle", cmd)


def test_shuffle_stays_whole_utterance():
    # Anchored like track nav — a keyword buried in ambient prose must not fire.
    assert not _matches("music_shuffle", "musisz przetasować karty zanim zaczniemy grę")


@pytest.mark.parametrize("cmd", [
    "co teraz gra?", "Jessica, co gra?", "co to za piosenka", "jaki to utwór",
    "co leci teraz",
])
def test_now_playing_questions_match(cmd):
    assert _matches("music_now_playing", cmd)


@pytest.mark.parametrize("cmd", ["shuffle", "shuffle the queue"])
def test_english_shuffle_matches(cmd):
    assert _matches("music_shuffle", cmd, lang="en")


@pytest.mark.parametrize("cmd", ["what's playing", "what song is this"])
def test_english_now_playing_matches(cmd):
    assert _matches("music_now_playing", cmd, lang="en")


# -- voice memos (2026-07-29) --------------------------------------------------
@pytest.mark.parametrize("cmd", [
    "Jessica, nagraj notatkę.", "nagraj notatkę głosową", "zostaw wiadomość",
    "zapisz notatkę",
])
def test_voice_memo_record_matches(cmd):
    assert _matches("voice_memo_record", cmd)


def test_inline_note_content_still_goes_to_remember(cmd="zapisz notatkę o zakupach"):
    # Content given inline is a TEXT note, not a dictation session.
    assert not _matches("voice_memo_record", cmd)
    assert _matches("remember_note", cmd)


def test_zapamietaj_stays_remember():
    assert not _matches("voice_memo_record", "zapamiętaj, że kod do bramy to cztery")
    assert _matches("remember_note", "zapamiętaj, że kod do bramy to cztery")


@pytest.mark.parametrize("cmd", ["record a voice note", "leave a message"])
def test_english_voice_memo_record_matches(cmd):
    assert _matches("voice_memo_record", cmd, lang="en")


@pytest.mark.parametrize("cmd", [
    "odtwórz notatki", "Jessica, odtwórz moje nagrania.", "puść notatki głosowe",
])
def test_voice_memo_play_matches(cmd):
    assert _matches("voice_memo_play", cmd)


def test_memo_play_does_not_hijack_music():
    # "puść muzykę / trójkę" must stay music/radio.
    assert not _matches("voice_memo_play", "puść muzykę")
    assert not _matches("voice_memo_play", "puść trójkę")


def test_play_found_recording_matches():
    assert _matches("voice_memo_play_last", "odtwórz nagranie")
    assert _matches("voice_memo_play_last", "Jessica, odtwórz to nagranie.")


@pytest.mark.parametrize("cmd,query", [
    ("co zapisałem o filtrze do wody", "filtrze do wody"),
    ("co nagrałam o zakupach?", "zakupach?"),
    ("znajdź w notatkach kod do bramy", "kod do bramy"),
])
def test_memory_search_matches_and_captures(cmd, query):
    assert _matches("memory_search", cmd)
    got = _named("memory_search", cmd, "query")
    assert got is not None and got.rstrip(".?!") == query.rstrip(".?!")


def test_memory_search_does_not_steal_library_search():
    # bare "znajdź coś spokojnego" stays a library search
    assert not _matches("memory_search", "znajdź coś spokojnego")
    assert _matches("library_search", "znajdź coś spokojnego")


@pytest.mark.parametrize("cmd", ["play my voice notes", "what did i save about the gate code",
                                 "search my notes for wifi"])
def test_english_memory_ux_matches(cmd):
    assert (_matches("voice_memo_play", cmd, lang="en")
            or _matches("memory_search", cmd, lang="en"))


# -- memory management (2026-08-04) --------------------------------------------
@pytest.mark.parametrize("cmd", [
    "ile mam notatek", "Jessica, ile mam nagrań?", "ile mam wspomnień",
])
def test_memory_count_matches(cmd):
    assert _matches("memory_count", cmd)


@pytest.mark.parametrize("cmd", [
    "usuń ostatnią notatkę", "Jessica, usuń ostatnie nagranie.", "skasuj notatkę",
])
def test_memory_delete_last_matches(cmd):
    assert _matches("memory_delete_last", cmd)


def test_ambient_usunalem_does_not_delete():
    # Past-tense narration must not fire deletion; nor content after the noun.
    assert not _matches("memory_delete_last", "usunąłem notatkę z lodówki wczoraj")
    assert not _matches("memory_delete_last", "usuń notatkę o zakupach z listy")


@pytest.mark.parametrize("cmd", ["how many notes do i have", "delete the last recording"])
def test_english_memory_management_matches(cmd):
    assert (_matches("memory_count", cmd, lang="en")
            or _matches("memory_delete_last", cmd, lang="en"))
