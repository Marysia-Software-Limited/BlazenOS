"""Tier 0 — the incremental sentence slicer for streaming TTS."""
from __future__ import annotations

from blazend.assistant.sentences import SentenceSlicer


def _feed_all(chunks: list[str]) -> list[str]:
    s = SentenceSlicer()
    out: list[str] = []
    for c in chunks:
        out += s.feed(c)
    out += s.flush()
    return out


def test_emits_sentence_only_once_terminal_plus_space_seen():
    s = SentenceSlicer()
    assert s.feed("Cześć") == []
    assert s.feed("!") == []          # terminal at end-of-buffer → wait
    assert s.feed(" Jak") == ["Cześć!"]  # space confirms the boundary
    assert s.flush() == ["Jak"]


def test_splits_multiple_sentences_in_one_chunk():
    out = _feed_all(["To jest pierwsze. To drugie! A trzecie?"])
    assert out == ["To jest pierwsze.", "To drugie!", "A trzecie?"]


def test_splits_across_token_boundaries():
    # Tokens arrive one fragment at a time, as from an LLM stream.
    out = _feed_all(["Dwa", " plus", " dwa", " to", " cztery", ".", " Koniec", "."])
    assert out == ["Dwa plus dwa to cztery.", "Koniec."]


def test_polish_abbreviations_do_not_split():
    out = _feed_all(["Kup mleko, chleb, jajka itd. ", "Reszta potem."])
    assert out == ["Kup mleko, chleb, jajka itd. Reszta potem."]


def test_np_abbreviation_mid_sentence():
    out = _feed_all(["Owoce, np. jabłka i gruszki, są zdrowe. ", "To wszystko."])
    assert out == ["Owoce, np. jabłka i gruszki, są zdrowe.", "To wszystko."]


def test_decimal_point_does_not_split():
    out = _feed_all(["Pi to około 3.14 w przybliżeniu. ", "Tak."])
    assert out == ["Pi to około 3.14 w przybliżeniu.", "Tak."]


def test_single_letter_initial_does_not_split():
    out = _feed_all(["Spotkałem J. Kowalskiego wczoraj. ", "Miło."])
    assert out == ["Spotkałem J. Kowalskiego wczoraj.", "Miło."]


def test_ellipsis_is_one_boundary():
    out = _feed_all(["No więc... ", "Tak myślę."])
    assert out == ["No więc...", "Tak myślę."]


def test_numbered_list_ordinal_stays_with_its_item():
    # "1." / "2." are list markers, not standalone sentences.
    out = _feed_all([
        "1. Koty są niezależne. ",
        "2. Lubią spać. ",
        "3. Mruczą.",
    ])
    assert out == ["1. Koty są niezależne.", "2. Lubią spać.", "3. Mruczą."]


def test_four_digit_year_still_ends_a_sentence():
    out = _feed_all(["To było w 2024. ", "Potem przyszedł rok 2025."])
    assert out == ["To było w 2024.", "Potem przyszedł rok 2025."]


def test_english_sentences_split_too():
    out = _feed_all(["Hello there. ", "How are you?"])
    assert out == ["Hello there.", "How are you?"]


def test_flush_returns_unterminated_tail():
    s = SentenceSlicer()
    assert s.feed("Reply with no terminator") == []
    assert s.flush() == ["Reply with no terminator"]
    assert s.flush() == []  # buffer drained


def test_empty_input_is_noop():
    s = SentenceSlicer()
    assert s.feed("") == []
    assert s.flush() == []
