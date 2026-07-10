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
@pytest.mark.parametrize("cmd", ["poprzedni", "poprzedni utwór", "cofnij", "wstecz"])
def test_real_prev_commands_match(cmd):
    assert _matches("music_prev", cmd)


@pytest.mark.parametrize("cmd", ["następny", "następny utwór", "dalej", "pomiń",
                                 "coś innego", "zagraj coś innego", "kolejny kawałek"])
def test_real_next_commands_match(cmd):
    assert _matches("music_next", cmd)


@pytest.mark.parametrize("cmd,ok", [("previous", True), ("next", True), ("skip", True),
                                    ("go back", True), ("the previous owner sold it", False)])
def test_english_next_prev(cmd, ok):
    assert (_matches("music_prev", cmd, "en") or _matches("music_next", cmd, "en")) is ok
