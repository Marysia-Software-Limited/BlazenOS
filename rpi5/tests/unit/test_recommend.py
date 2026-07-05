"""Unit tests for the book/music recommendation RAG + prompt rendering."""
from __future__ import annotations

import json

import pytest

from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.prompts import (
    PromptLibrary,
    format_candidates,
    parse_choice,
)
from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.recommend import (
    Ontology,
    RecommendationEngine,
)

_ONTOLOGY = {
    "genre_synonyms": {"przygodowe": ["przygodow"], "poezja": ["wiersz", "sonet"],
                       "wiersze": ["wiersz"]},
    "epoch_synonyms": {"romantyczne": ["romantyzm"], "klasyka": ["renesans", "starozytnosc"]},
    "nationality_synonyms": {"francuska": "francuski", "rosyjska": "rosyjski"},
    "author_nationality": {"Aleksander Dumas (ojciec)": "francuski", "Honoré de Balzac": "francuski"},
}


@pytest.fixture()
def ontology(tmp_path):
    p = tmp_path / "books.json"
    p.write_text(json.dumps(_ONTOLOGY, ensure_ascii=False), encoding="utf-8")
    return Ontology(path=str(p))


def test_parse_signals(ontology):
    s = ontology.parse("coś przygodowego")
    assert s.genres == ["przygodow"] and not s.nationalities
    s = ontology.parse("francuska klasyka")
    assert s.nationalities == ["francuski"] and "renesans" in s.epochs
    # embedded-word false positive guard: "romantyczne" must not trigger "antyczne"
    s = ontology.parse("romantyczne wiersze")
    assert s.epochs == ["romantyzm"] and "wiersz" in s.genres


class _Sem:
    def __init__(self, items):
        self._items = items

    def search(self, query, *, k, kinds=None):
        return list(self._items)

    def count(self, kind=None):
        return len(self._items)


def test_nationality_hard_prefer(ontology):
    # A French novel scores LOWER by cosine than an unrelated poem.
    sem = _Sem([
        {"type": "book", "title": "Wiersz", "who": "Anon", "genre": "Wiersz", "score": 0.9},
        {"type": "book", "title": "Trzej muszkieterowie", "who": "Aleksander Dumas (ojciec)",
         "genre": "powieść", "score": 0.5},
    ])
    eng = RecommendationEngine(semantic=sem, ontology=ontology)
    top = eng.candidates("francuska klasyka", kinds=("book",), k=2)
    assert top[0]["who"].startswith("Aleksander Dumas")  # nationality beat cosine


def test_genre_boost_orders_candidates(ontology):
    sem = _Sem([
        {"type": "book", "title": "A", "who": "X", "genre": "Wiersz", "score": 0.70},
        {"type": "book", "title": "B", "who": "Y", "genre": "powieść przygodowa", "score": 0.65},
    ])
    eng = RecommendationEngine(semantic=sem, ontology=ontology)
    top = eng.candidates("coś przygodowego", kinds=("book",), k=2)
    assert top[0]["title"] == "B"  # +genre boost overtakes the slightly higher cosine


def test_prompt_render_and_parse():
    lib = PromptLibrary(prompts_dir="/nonexistent")  # built-in defaults
    cands = [{"title": "Kim", "who": "Kipling", "genre": "powieść"},
             {"title": "Ania", "who": "Montgomery"}]
    system, user = lib.render("book_recommendation", query="przygoda",
                              candidates=format_candidates(cands))
    assert "Jessica" in system and "1. Kim" in user and "przygoda" in user
    idx, pitch = parse_choice("NUMER: 2\nPOLECAM: Świetna na wieczór.", len(cands))
    assert idx == 1 and pitch == "Świetna na wieczór."
    # out-of-range / unparseable → clamp to first, whole text as pitch
    idx, pitch = parse_choice("hmm nie wiem", 2)
    assert idx == 0 and "hmm" in pitch
