"""Tiny bilingual (PL + EN) natural-time parser for reminders.

Handles the common spoken forms; returns ``None`` when it can't tell, so the
engine can ask the user to rephrase. All parsing is relative to an injected
``now`` so reminder firing is deterministic in tests.

Supported:
  * relative — "za 10 minut", "za godzinę", "za pół godziny", "za 5 sekund",
    "in 10 minutes", "in 2 hours", "in 30 seconds"
  * clock today/next — "o 15:00", "o 9", "at 3pm", "at 15:30"
  * tomorrow — "jutro o 9:00", "tomorrow at 8"
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

_REL = re.compile(
    r"\b(?:za|in)\s+(?:(\d+)\s+)?"
    r"(sekund\w*|second\w*|minut\w*|minute\w*|min\b|godzin\w*|hour\w*|"
    r"p[oó][lł]\s+godziny)",
    re.IGNORECASE,
)
_CLOCK = re.compile(
    r"\b(?:o|at)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b",
    re.IGNORECASE,
)
_TOMORROW = re.compile(r"\b(jutro|tomorrow)\b", re.IGNORECASE)


def _unit_seconds(n: int, unit: str) -> int | None:
    u = unit.lower()
    if u.startswith(("sekund", "second")):
        return n
    if u.startswith(("minut", "minute", "min")):
        return n * 60
    if u.startswith("pół") or u.startswith("pol") or u.startswith("pól"):
        return 30 * 60  # "pół godziny" → 30 min (number ignored)
    if u.startswith(("godzin", "hour")):
        return n * 3600
    return None


def parse_when(text: str, now: datetime) -> datetime | None:
    """Parse a spoken time expression to an absolute ``datetime`` (or None)."""
    # 1) relative offsets: "za 10 minut", "in 2 hours", "za godzinę".
    m = _REL.search(text)
    if m:
        n = int(m.group(1)) if m.group(1) else 1
        secs = _unit_seconds(n, m.group(2))
        if secs is not None:
            return now + timedelta(seconds=secs)

    # 2) clock time, optionally "jutro/tomorrow".
    m = _CLOCK.search(text)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2)) if m.group(2) else 0
        ampm = (m.group(3) or "").lower()
        if ampm == "pm" and hour < 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if _TOMORROW.search(text):
            target += timedelta(days=1)
        elif target <= now:
            target += timedelta(days=1)  # next occurrence
        return target

    return None
