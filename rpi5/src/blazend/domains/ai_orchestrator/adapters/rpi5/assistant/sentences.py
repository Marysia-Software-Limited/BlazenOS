"""Incremental sentence slicer — cut a streaming LLM token feed into sentences.

The point is *perceived* latency: as the local LLM emits tokens, we want to
hand each finished sentence to Piper TTS the instant it completes, so audio
starts after the first sentence (~a couple of seconds) instead of after the
whole reply (tens of seconds on the Pi CPU). See `docs/04-VOICE-PIPELINE.md`.

:class:`SentenceSlicer` is fed arbitrary text chunks (one or many tokens) and
yields complete sentences as soon as a terminal ``. ? ! …`` is followed by
whitespace. It is **Polish-first and abbreviation-aware**: it will not cut on
``np.``, ``itd.``, ``godz.``, a decimal point (``3.14``), or a single-letter
initial (``J. Kowalski``), which would otherwise fragment Polish speech. Pure
and synchronous — fully unit-testable with no model.
"""

from __future__ import annotations

import re

# Abbreviations (stored without the trailing dot, lower-cased) after which a
# period is NOT a sentence end. Polish first, then a few English ones.
_ABBREV: frozenset[str] = frozenset({
    # Polish
    "np", "itd", "itp", "tj", "tzn", "m", "in", "dr", "prof", "godz", "ul",
    "nr", "str", "wg", "pkt", "art", "mln", "mld", "tys", "zł", "r", "w",
    "płk", "gen", "inż", "mgr", "św", "ok", "por", "ww", "cd", "tzw",
    # English
    "mr", "mrs", "ms", "vs", "eg", "ie", "etc", "no", "st", "approx",
})

_TERMINAL = re.compile(r"[.?!…]+")
_TRAILING_LETTERS = re.compile(r"([^\W\d_]+)$", re.UNICODE)
_TRAILING_DIGITS = re.compile(r"(\d+)$")


class SentenceSlicer:
    """Accumulates streamed text and emits complete sentences as they finish."""

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, chunk: str) -> list[str]:
        """Add a text chunk; return any sentences that just completed."""
        if chunk:
            self._buf += chunk
        return self._extract()

    def flush(self) -> list[str]:
        """Return the leftover tail (an unterminated final sentence), if any."""
        tail = self._buf.strip()
        self._buf = ""
        return [tail] if tail else []

    # -- internals -----------------------------------------------------
    def _extract(self) -> list[str]:
        out: list[str] = []
        while True:
            cut = self._next_boundary(self._buf)
            if cut is None:
                break
            sentence = self._buf[: cut + 1].strip()
            self._buf = self._buf[cut + 1 :].lstrip()
            if sentence:
                out.append(sentence)
        return out

    def _next_boundary(self, s: str) -> int | None:
        """Index of the last punctuation char of the first real sentence end."""
        for m in _TERMINAL.finditer(s):
            end = m.end() - 1  # index of the final terminal char in the run
            # Need to *see* what follows: only a sentence end if whitespace
            # already arrived. A terminal at the very end of the buffer might be
            # mid-number ("3.") or have more text coming — wait (flush covers it).
            if end + 1 >= len(s):
                continue
            if not s[end + 1].isspace():
                continue  # e.g. "3.14" or "U.S.A" — '.' glued to a non-space
            if self._is_abbreviation(s, m.start()) or self._is_list_ordinal(s, m.start()):
                continue
            return end
        return None

    @staticmethod
    def _is_abbreviation(s: str, dot_start: int) -> bool:
        """True if the word just before the period is a known abbrev/initial."""
        word_match = _TRAILING_LETTERS.search(s[:dot_start])
        if not word_match:
            return False
        word = word_match.group(1)
        if word.lower() in _ABBREV:
            return True
        # A lone capital letter before the dot is almost always an initial.
        return len(word) == 1 and word.isupper()

    @staticmethod
    def _is_list_ordinal(s: str, dot_start: int) -> bool:
        """True for a short numbered-list marker (``1.`` ``2.``) — not a stop.

        Keeps the ordinal glued to its item ("1. Koty…") instead of speaking a
        bare "one." Only 1–2 digit runs qualify, so a year ("2024.") still ends
        a sentence.
        """
        digits = _TRAILING_DIGITS.search(s[:dot_start])
        return digits is not None and len(digits.group(1)) <= 2


__all__ = ["SentenceSlicer"]
