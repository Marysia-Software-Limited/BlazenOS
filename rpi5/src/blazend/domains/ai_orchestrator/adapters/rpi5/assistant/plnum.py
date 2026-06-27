"""Polish number/time words for natural TTS pronunciation.

Piper reads bare digits like ``23:16`` badly (letter/symbol values), so the
assistant spells clock times out as words — ``dwudziesta trzecia szesnaście`` —
before they reach TTS. Only the ranges the assistant needs are covered: clock
hours 0–23 (feminine ordinals, because *godzina* is feminine) and minutes 0–59
(cardinals). Deterministic and instant — no LLM round-trip.
"""

from __future__ import annotations

# Cardinals 0–19 (minutes; also the units for 20–59).
_ONES = [
    "zero", "jeden", "dwa", "trzy", "cztery", "pięć", "sześć", "siedem",
    "osiem", "dziewięć", "dziesięć", "jedenaście", "dwanaście", "trzynaście",
    "czternaście", "piętnaście", "szesnaście", "siedemnaście", "osiemnaście",
    "dziewiętnaście",
]
_TENS = {2: "dwadzieścia", 3: "trzydzieści", 4: "czterdzieści", 5: "pięćdziesiąt"}

# Feminine ordinal hours 0–23 ("(godzina) dwudziesta trzecia").
_HOUR_ORD = [
    "zerowa", "pierwsza", "druga", "trzecia", "czwarta", "piąta", "szósta",
    "siódma", "ósma", "dziewiąta", "dziesiąta", "jedenasta", "dwunasta",
    "trzynasta", "czternasta", "piętnasta", "szesnasta", "siedemnasta",
    "osiemnasta", "dziewiętnasta", "dwudziesta", "dwudziesta pierwsza",
    "dwudziesta druga", "dwudziesta trzecia",
]


def cardinal(n: int) -> str:
    """Cardinal 0–59 in Polish words ("szesnaście", "dwadzieścia jeden")."""
    n %= 60
    if n < 20:
        return _ONES[n]
    tens, units = divmod(n, 10)
    word = _TENS[tens]
    return word if units == 0 else f"{word} {_ONES[units]}"


def hour_ordinal(h: int) -> str:
    """Feminine ordinal hour 0–23 ("dwudziesta trzecia")."""
    return _HOUR_ORD[h % 24]


def time_words(hour: int, minute: int) -> str:
    """Spoken Polish clock time. 23:16 → "dwudziesta trzecia szesnaście"; a whole
    hour drops the minutes (23:00 → "dwudziesta trzecia"); single-digit minutes
    keep the leading "zero" (23:05 → "dwudziesta trzecia zero pięć")."""
    h = hour_ordinal(hour)
    if minute == 0:
        return h
    if 1 <= minute <= 9:
        return f"{h} zero {cardinal(minute)}"
    return f"{h} {cardinal(minute)}"
