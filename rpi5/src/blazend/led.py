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
YELLOW = "yellow"    # reprompt / degraded — please repeat
RED = "red"          # error / recovery mode (SSH already on)

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
    if topic == "health.status":
        level = data.get("level")
        if level == "degraded":
            return YELLOW
        if level in ("recovery", "critical"):
            return RED
        if level == "ok":
            return GREEN
        return None
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


# --- Per-phase LEDs: 3 physical LEDs, one per pipeline part --------------
# The ReSpeaker 2-Mics HAT has 3 RGB LEDs; map one to each stage of a turn so a
# screenless dev can watch it flow down the board: LED 0 = LISTEN (mic/wake/
# ASR), LED 1 = THINK (NLU/brain/LLM), LED 2 = SPEAK (TTS/playback).
LED_PHASES = ("listen", "think", "speak")
IDLE_LEDS: list[str] = [GREEN, OFF, OFF]   # at rest: listening for the wake word


def pipeline_colors(
    leds: list[str], topic: str, data: dict[str, Any] | None = None
) -> list[str]:
    """Apply one observed IPC event to the 3 per-phase LED colours.

    Pure function over the previous slot list — returns the new ``[listen,
    think, speak]`` colours. A turn flows: wake → LISTEN blue, ASR → THINK
    magenta, TTS → SPEAK blue; faults paint all three red/yellow.
    """
    data = data or {}
    out = list(leds)
    if topic == "error":
        return [RED, RED, RED]
    if topic == "health.status":
        level = data.get("level")
        if level in ("recovery", "critical"):
            return [RED, RED, RED]
        if level == "degraded":
            return [YELLOW, YELLOW, YELLOW]
        return out  # ok → keep the per-phase state
    if topic == "system.event":
        kind = data.get("kind")
        if kind == "sleep":
            return [OFF, OFF, OFF]
        if kind in ("ready", "heartbeat", "resume"):
            out[0] = GREEN  # listening for the wake word
            return out
        return out
    if topic in ("wake.detected", "vad.start"):
        return [BLUE, OFF, OFF]           # new turn: capturing; clear downstream
    if topic == "vad.end":
        out[0] = GREEN
        return out
    if topic in ("asr.partial", "asr.final", "nlu.intent", "nlu.miss"):
        out[0], out[1] = GREEN, MAGENTA   # heard you → thinking
        return out
    if topic == "brain.reply":
        if data.get("final_"):
            out[1], out[2] = OFF, BLUE    # done thinking → speaking
        else:
            out[1] = MAGENTA
        return out
    if topic == "tts.frame":
        out[1], out[2] = OFF, BLUE        # speaking
        return out
    return out


class PipelineLeds:
    """Tracks the 3 per-phase LED colours from the event stream and persists
    them to ``led.json`` (with the legacy single ``color`` for older tools)."""

    def __init__(self, path: Path, *, initial: list[str] | None = None):
        self.path = Path(path)
        self.leds: list[str] = list(initial) if initial else list(IDLE_LEDS)

    def observe(self, topic: str, data: dict[str, Any] | None = None) -> bool:
        """Update the 3 colours from an event. Returns True if any changed."""
        new = pipeline_colors(self.leds, topic, data)
        if new == self.leds:
            return False
        self.leds = new
        return True

    def write(self) -> None:
        """Persist the 3 colours to ``led.json`` (atomic)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        active = next((c for c in self.leds if c != OFF), OFF)  # legacy surface
        payload = {
            "v": 2,
            "leds": self.leds,
            "phases": list(LED_PHASES),
            "color": active,
            "meaning": {
                LED_PHASES[i]: MEANING.get(self.leds[i], self.leds[i])
                for i in range(min(len(LED_PHASES), len(self.leds)))
            },
        }
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(self.path)


__all__ = [
    "LedSimulator", "color_for", "MEANING", "OFF", "GREEN", "BLUE", "MAGENTA",
    "YELLOW", "RED", "PipelineLeds", "pipeline_colors", "LED_PHASES", "IDLE_LEDS",
]
