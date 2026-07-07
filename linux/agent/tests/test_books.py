"""Unit tests for the book reader's text cleaning + chunking (no ebooklib / TTS)."""
from __future__ import annotations

from jessica_linux.books import chunks, clean_text


def test_clean_text_strips_artifacts():
    # soft hyphen inside a word, mid-paragraph line breaks, (...) marks, page number
    dirty = "Ciem­ny tunel\nmetra\n„(...)” cisza.\n\n42\n\nDrugi akapit."
    out = clean_text(dirty)
    assert "Ciemny tunel metra" in out          # soft hyphen removed, line breaks joined
    assert "(...)" not in out and "…" not in out  # editorial marks gone
    assert "\n42\n" not in out and "\n\n42" not in out  # standalone page number dropped
    assert "\n\nDrugi akapit." in out           # paragraph break preserved


def test_clean_text_keeps_numbers_in_titles_and_inline():
    assert clean_text("Metro\n2033 Gluchovsky") == "Metro 2033 Gluchovsky"  # joined, not dropped
    assert "rok 1984" in clean_text("Był\nrok 1984 wtedy.")


def test_chunks_respect_max_and_split_paragraphs():
    chapter = "Akapit pierwszy.\n\n" + ("A" * 500) + "\n\n" + ("B" * 500)
    out = chunks([chapter], max_chars=700)
    assert len(out) >= 2
    assert all(len(c) <= 700 for c in out)
    # content is preserved (order + text), just repackaged
    assert "Akapit pierwszy." in out[0]
    assert any("B" * 100 in c for c in out)


def test_long_paragraph_is_sentence_split():
    giant = ". ".join(f"Zdanie numer {i}" for i in range(200)) + "."
    out = chunks([giant], max_chars=300)
    assert out and all(len(c) <= 300 for c in out)
    # sentence boundaries preferred — most chunks end at a period
    assert sum(c.endswith(".") for c in out) >= len(out) - 1


def test_empty_and_tiny_chapters():
    assert chunks([]) == []
    assert chunks(["krótko"], max_chars=700) == ["krótko"]
