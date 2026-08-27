"""Recovery policy — map a watchdog `health.status` level to LED + speech.

`blazend-health` (Rust) emits a `health.status` verdict (`ok` / `degraded` /
`recovery` / `critical`); the orchestrator turns it into the LED colour and a
spoken recovery cue. A fault has no user utterance to language-detect from, so
the announcement uses the **effective language** (a voice pin, else the system
default — Polish). Announcements are bilingual, Polish-first.
"""

from __future__ import annotations

from dataclasses import dataclass

from blazend.domains.systems.adapters.rpi5.led import GREEN, RED, YELLOW
from blazend.i18n import msg


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
        return RecoveryAnnouncement("degraded", YELLOW, msg("health.degraded", lang))
    if level == "recovery":
        return RecoveryAnnouncement("recovery", RED, msg("health.recovery", lang))
    if level == "critical":
        return RecoveryAnnouncement(
            "critical", RED, msg("health.critical", lang), recovery_image=True)
    return RecoveryAnnouncement("ok", GREEN, "")


__all__ = ["RecoveryAnnouncement", "for_level"]
