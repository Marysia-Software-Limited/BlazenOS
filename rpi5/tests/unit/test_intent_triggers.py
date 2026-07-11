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


def test_general_weather_does_not_hijack_to_rain():
    # "jaka pogoda" / temperature go to weather_query, not rain_forecast.
    for q in ("jaka jest pogoda", "jaka pogoda w Krakowie", "jaka temperatura"):
        assert _matches("weather_query", q), q
        assert not _matches("rain_forecast", q), q
