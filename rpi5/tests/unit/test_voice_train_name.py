"""Unit tests for the voice-training finale: learning how to address the owner.

`_extract_address` turns the spoken answer to "jak mam się do ciebie zwracać?"
into the profile address form. Offline, no audio. PL + EN parity per
CLAUDE.md §8.
"""

from __future__ import annotations

import pytest

from blazend.domains.systems.adapters.rpi5.orchestrator.supervisor import _extract_address


@pytest.mark.parametrize(("spoken", "expected"), [
    ("Błazen", "Błazen"),
    ("błazen.", "Błazen"),
    ("Mów mi Błazen.", "Błazen"),
    ("mów do mnie Błazen", "Błazen"),
    ("nazywaj mnie Błazen", "Błazen"),
    ("zwracaj się do mnie per pan doktor", "Pan doktor"),
    ("mam na imię Marek", "Marek"),
    ("jestem Marek", "Marek"),
])
def test_polish_answers(spoken, expected):
    assert _extract_address(spoken) == expected


@pytest.mark.parametrize(("spoken", "expected"), [
    ("Ben", "Ben"),
    ("call me Ben", "Ben"),
    ("my name is Ben", "Ben"),
    ("I'm Ben", "Ben"),
])
def test_english_answers(spoken, expected):
    assert _extract_address(spoken) == expected


def test_rambling_answer_is_rejected():
    # More than four words is a mis-capture, not an address — the caller
    # retries instead of storing a sentence as the owner's name.
    assert _extract_address("no wiesz, to zależy od dnia i pory roku") == ""


def test_empty_answer_is_rejected():
    assert _extract_address("") == ""
    assert _extract_address("  .,! ") == ""


def test_silence_hallucinations_never_become_the_address():
    # Live 2026-09-08 15:56: an empty name window transcribed to whisper's
    # stock "Dziękuje za uwagę" and Jessica addressed the owner with it.
    assert _extract_address("Dziękuje za uwagę") == ""
    assert _extract_address("Dziękuję za oglądanie.") == ""
    assert _extract_address("Zapraszam na kolejny film") == ""
    assert _extract_address("Mów mi Błazen") == "Błazen"  # real names still pass
