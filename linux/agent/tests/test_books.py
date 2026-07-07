"""Unit tests for the book reader's chunking (no ebooklib / TTS / audio)."""
from __future__ import annotations

from jessica_linux.books import chunks


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
