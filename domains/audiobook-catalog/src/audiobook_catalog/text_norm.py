"""Polish accent-fold + crude stemming for spoken-title matching.

Moved verbatim from rpi5 ``assistant/radio.py`` so the audiobook resolver here
and the radio resolver there share one source of truth (domains for common
code). Pure stdlib — no third-party or intra-repo imports.
"""
from __future__ import annotations

import re
import unicodedata

_VOWELS = frozenset("aeiouy")


def _fold(text: str) -> str:
    """Lower-case and strip diacritics ('Trójkę' → 'trojke')."""
    nfkd = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _stem_token(tok: str) -> str:
    """Crudely drop one trailing vowel so Polish case endings collapse
    ('trojke'/'trojka'/'trojki' → 'trojk'). Only for tokens long enough that
    the stem stays meaningful."""
    return tok[:-1] if len(tok) > 3 and tok[-1] in _VOWELS else tok


def _stem_phrase(text: str) -> str:
    """Fold, tokenise and stem; return a space-delimited token string padded
    with spaces so callers can test whole-token containment."""
    toks = re.findall(r"[a-z0-9]+", _fold(text))
    return " " + " ".join(_stem_token(t) for t in toks) + " "


__all__ = ["_VOWELS", "_fold", "_stem_token", "_stem_phrase"]
