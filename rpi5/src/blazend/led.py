"""LED status simulator — the headless status surface.

The appliance has no screen; status is shown out-of-band via the HAT's RGB
LED (see `docs/02-HARDWARE.md` status table). In the VM / on the dev host
there is no GPIO, so the orchestrator writes the same status to
`/run/blazen/led.json` for tests and tooling. On real hardware a GPIO driver
consumes the identical colour contract.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Colour contract — keep in sync with docs/02-HARDWARE.md.
OFF = "off"          # asleep / not listening
GREEN = "green"      # listening for the wake word
BLUE = "blue"        # wake detected, capturing the utterance
MAGENTA = "magenta"  # processing (VAD / ASR / NLU / LLM / TTS)
YELLOW = "yellow"    # reprompt — please repeat
RED = "red"          # error — SSH recovery enabled

MEANING = {
    OFF: "asleep",
    GREEN: "listening",
    BLUE: "capturing",
    MAGENTA: "processing",
    YELLOW: "reprompt",
    RED: "error",
}


def color_for(topic: str, data: dict[str, Any] | None = None) -> str | None:
    """Map an observed IPC event to an LED colour. ``None`` = no change."""
    data = data or {}
    if topic == "error":
        return RED
    if topic in ("wake.detected", "vad.start"):
        return BLUE
    if topic in ("vad.end", "asr.partial", "asr.final", "nlu.intent", "tts.frame"):
        return MAGENTA
    if topic == "brain.reply":
        # Final token → reply delivered → back to listening.
        return GREEN if data.get("final_") else MAGENTA
    if topic == "system.event":
        kind = data.get("kind")
        if kind in ("ready", "heartbeat", "resume"):
            return GREEN
        if kind == "reprompt":
            return YELLOW
        if kind == "sleep":
            return OFF
    return None


class LedSimulator:
    """Tracks the current LED colour and persists it to ``led.json``."""

    def __init__(self, path: Path, *, initial: str = OFF):
        self.path = Path(path)
        self.color = initial

    def observe(self, topic: str, data: dict[str, Any] | None = None) -> bool:
        """Update colour from an event. Returns True if the colour changed."""
        new = color_for(topic, data)
        if new is None or new == self.color:
            return False
        self.color = new
        return True

    def write(self) -> None:
        """Persist the current colour to ``led.json`` (atomic)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"v": 1, "color": self.color, "meaning": MEANING[self.color]}
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(self.path)


__all__ = ["LedSimulator", "color_for", "MEANING", "OFF", "GREEN", "BLUE", "MAGENTA", "YELLOW", "RED"]
