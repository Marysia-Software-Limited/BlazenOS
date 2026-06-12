"""Name / wake detection — reacting to "Jessica".

In the appliance the wake word fires from the audio path (`blazend-wake`,
openWakeWord). In the text prototype we detect the assistant's name at the
start of (or anywhere in) the transcript, matching the same bilingual names
the wake models use. See `configs/wake-word.yaml` and
`docs/product/02-PERSONA-AND-WAKE.md`.
"""

from __future__ import annotations

import re

# Bilingual address forms, mirroring the active wake models
# (jessica_en / jessica_pl) in configs/wake-word.yaml. All ASCII, so a
# case-insensitive match on the original text is exact.
WAKE_NAMES: tuple[str, ...] = (
    "hej jessico",
    "hej jessica",
    "jessico",
    "jessica",
    "jess",
)

# Longest-first so "hej jessico" wins over "jessico"/"jess".
_ORDERED = sorted(WAKE_NAMES, key=len, reverse=True)
_WAKE_RE = re.compile(
    r"\b(" + "|".join(re.escape(n) for n in _ORDERED) + r")\b",
    re.IGNORECASE,
)


def is_wake(text: str) -> bool:
    """True if the transcript addresses the assistant by name."""
    return _WAKE_RE.search(text) is not None


def strip_wake(text: str) -> str:
    """Remove a leading name + filler so the command can be routed.

    "Hej Jessico, jaka jest pogoda?" → "jaka jest pogoda?".
    If no name is present (or nothing follows it) the text is returned
    unchanged.
    """
    m = _WAKE_RE.search(text)
    if m is None:
        return text.strip()
    rest = (text[: m.start()] + " " + text[m.end():]).strip()
    rest = rest.lstrip(" ,.!?–-—:;").strip()
    return rest or text.strip()
