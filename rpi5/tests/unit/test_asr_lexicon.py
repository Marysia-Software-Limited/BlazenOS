"""Unit tests for the ASR domain lexicon (`asr/lexicon.py`).

Offline: catalogs are the repo configs plus temp JSON files; no model, no
network. PL + EN parity per CLAUDE.md §8 — the corrector must repair both
Polish and English near-misses (EN stays asset-retained even though runtime
is Polish-only).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from blazend.domains.voice_input.adapters.rpi5.asr.lexicon import (
    Lexicon,
    _expand_trigger,
)

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _config_root(monkeypatch, tmp_path):
    monkeypatch.setenv("BLAZEN_CONFIG_ROOT", str(REPO / "configs"))
    # Hermetic: never read a real /var/lib/blazen off the dev box.
    monkeypatch.setenv("BLAZEN_MUSIC_INDEX", str(tmp_path / "no-music.json"))
    monkeypatch.setenv("BLAZEN_AUDIOBOOKS_CATALOG", str(tmp_path / "no-books.json"))
    monkeypatch.setenv("BLAZEN_ONTOLOGY", str(REPO / "configs/ontology/books.json"))


# ----- trigger expansion ------------------------------------------------------


def test_expand_diacritic_groups_diacritic_variant_first():
    out = _expand_trigger("g(ł|l)o(ś|s)niej")
    assert out[0] == "głośniej"  # canonical spelling leads
    assert "glosniej" in [v.replace("ł", "l") for v in out] or len(out) == 4


def test_expand_anchored_alternation_yields_each_word():
    out = _expand_trigger("^(stop|cicho|wystarczy)$")
    assert out == ["stop", "cicho", "wystarczy"]


def test_expand_optional_group():
    out = _expand_trigger("say (that|it) again")
    assert "say that again" in out and "say it again" in out
    assert "volume up" in _expand_trigger("(volume|louder) (up|higher)?")


def test_expand_skips_regexy_patterns():
    assert _expand_trigger(r"przypomnij .* o \d+") == []
    # A bare `,?` outside any group survives group expansion — must be dropped.
    assert all("?" not in v for v in _expand_trigger("jessica,? (what|who)"))


# ----- correction -------------------------------------------------------------


def test_correct_repairs_real_misread_stop():
    # Observed live 2026-08-27 (paul, radio playing): "Dżesika, stop!" came
    # back from whisper as "Jeszcze karp! Top!" (conf 0.39).
    out, hits = Lexicon.build().correct("Jeszcze karp! Top!")
    assert "stop" in out
    assert hits


def test_correct_repairs_station_name_pl():
    out, _ = Lexicon.build().correct("włącz trujke")
    assert "Trójka" in out
    out2, _ = Lexicon.build().correct("włącz radio nowy śfiat")
    assert "Radio Nowy Świat" in out2


def test_correct_repairs_english_command():
    out, _ = Lexicon.build().correct("which time is it")
    assert out == "what time is it"


def test_correct_leaves_exact_transcripts_alone():
    lex = Lexicon.build()
    for text in ("Jessica, włącz trójkę.", "zagraj radio kraków", "głośniej",
                 "Dżesika, zatrzymaj muzykę"):
        out, hits = lex.correct(text)
        assert out == text
        assert not hits


def test_correct_never_spans_punctuation():
    # "Jessica, włącz" glued across the comma must NOT fuzzy-repair into a
    # lookalike command phrase and eat the verb.
    out, _ = Lexicon.build().correct("Jessica, włącz trójkę.")
    assert "włącz" in out


def test_correct_uses_music_catalog(monkeypatch, tmp_path):
    index = tmp_path / "music-index.json"
    index.write_text(json.dumps({
        "version": 1,
        "tracks": [{"title": "Autobiografia", "artist": "Perfect", "album": "UNU"}],
    }), encoding="utf-8")
    monkeypatch.setenv("BLAZEN_MUSIC_INDEX", str(index))
    out, hits = Lexicon.build().correct("zagraj autobiografia perfekt")
    assert "Perfect" in out
    assert hits


def test_missing_catalogs_never_break_build(monkeypatch, tmp_path):
    monkeypatch.setenv("BLAZEN_ONTOLOGY", str(tmp_path / "missing.json"))
    lex = Lexicon.build()
    assert lex.tiers  # intents/radio from repo configs still present


# ----- hotwords ---------------------------------------------------------------


def test_hotwords_lead_with_wake_and_respect_cap():
    hw = Lexicon.build().hotwords(120)
    assert hw.startswith("Dżesika, Jessica")  # Polish-first, wake name first
    assert "Trójka" in hw  # station proper names beat their aliases
    assert len(hw) <= 120


def test_hotwords_tiny_budget_is_safe():
    hw = Lexicon.build().hotwords(10)
    assert hw == "Dżesika"


def test_common_words_never_repaired_into_entities():
    # Live incident 2026-08-29 15:04: ambient "…to jednak zwiększa…" had
    # "jednak" repaired to the station "Jedynka" (ratio 0.83) inside a
    # false-wake window — and the radio started unasked. Function words are
    # never a misheard entity.
    lex = Lexicon.build()
    for text in ("to jednak zwiększa", "jutro będzie lepiej", "no dobra",
                 "jeszcze chwila", "przecież wiesz"):
        out, hits = lex.correct(text)
        assert out == text, f"{text!r} got repaired: {hits}"


def test_genuine_station_typo_still_repaired():
    # The stoplist must not kill real entity repairs.
    out, _ = Lexicon.build().correct("włącz jedynkie")
    assert "Jedynka" in out
