"""Tier 0 — LED status simulator (the headless status surface)."""
from __future__ import annotations

import json

from blazend.domains.systems.adapters.rpi5 import led
from blazend.domains.systems.adapters.rpi5.led import LedSimulator


def test_color_for_mapping():
    assert led.color_for("error") == led.RED
    assert led.color_for("wake.detected") == led.BLUE
    assert led.color_for("asr.final") == led.MAGENTA
    assert led.color_for("nlu.intent") == led.MAGENTA
    assert led.color_for("brain.reply", {"final_": False}) == led.MAGENTA
    assert led.color_for("brain.reply", {"final_": True}) == led.GREEN
    assert led.color_for("system.event", {"kind": "heartbeat"}) == led.GREEN
    assert led.color_for("system.event", {"kind": "sleep"}) == led.OFF
    assert led.color_for("system.event", {"kind": "observed"}) is None  # ignored


def test_simulator_transitions_and_file(tmp_path):
    sim = LedSimulator(tmp_path / "led.json")
    assert sim.color == led.OFF

    # boot → listening
    assert sim.observe("system.event", {"kind": "heartbeat"}) is True
    sim.write()
    assert json.loads((tmp_path / "led.json").read_text()) == {
        "v": 1, "color": "green", "meaning": "listening",
    }

    # wake → capturing; same colour twice = no change
    assert sim.observe("wake.detected", {"model": "jessica_pl"}) is True
    assert sim.color == led.BLUE
    assert sim.observe("vad.start") is False  # already blue

    # processing → error → back to listening
    assert sim.observe("asr.final", {"text": "..."}) is True
    assert sim.color == led.MAGENTA
    assert sim.observe("error", {"code": "asr.no_text"}) is True
    assert sim.color == led.RED
    assert sim.observe("brain.reply", {"final_": True}) is True
    assert sim.color == led.GREEN

    # unmapped event → no change
    assert sim.observe("audio.frame") is False
