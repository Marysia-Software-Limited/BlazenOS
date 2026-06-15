"""Recovery policy — map a watchdog `health.status` level to LED + speech.

`blazend-health` (Rust) emits a `health.status` verdict (`ok` / `degraded` /
`recovery` / `critical`); the orchestrator turns it into the LED colour and a
spoken recovery cue. A fault has no user utterance to language-detect from, so
the announcement uses the **effective language** (a voice pin, else the system
default — Polish). Announcements are bilingual, Polish-first.
"""

from __future__ import annotations

from dataclasses import dataclass

from blazend.led import GREEN, RED, YELLOW


def _t(lang: str, pl: str, en: str) -> str:
    return pl if lang == "pl" else en


@dataclass
class RecoveryAnnouncement:
    """What the orchestrator should do for a given health level."""

    level: str
    color: str
    speak: str = ""                 # "" → no spoken announcement (ok)
    recovery_image: bool = False    # reboot into the read-only recovery image


def for_level(level: str, lang: str = "pl") -> RecoveryAnnouncement:
    """LED colour + bilingual spoken cue for a `health.status` level."""
    if level == "degraded":
        return RecoveryAnnouncement(
            "degraded", YELLOW,
            _t(lang, "Coś się zacięło, już wracam.", "I'm stuck — one moment."))
    if level == "recovery":
        return RecoveryAnnouncement(
            "recovery", RED,
            _t(lang, "Tryb awaryjny. Połączenie SSH jest dostępne.",
                     "Recovery mode. SSH is available."))
    if level == "critical":
        return RecoveryAnnouncement(
            "critical", RED,
            _t(lang, "Błąd krytyczny. Uruchamiam tryb ratunkowy.",
                     "Critical error. Booting recovery mode."),
            recovery_image=True)
    return RecoveryAnnouncement("ok", GREEN, "")


__all__ = ["RecoveryAnnouncement", "for_level"]
