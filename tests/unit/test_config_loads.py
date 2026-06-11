"""Tier 0 — every shipped YAML config loads and has its top-level keys."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

CFG_DIR = Path(__file__).resolve().parents[2] / "configs"

EXPECTED = {
    "system.yaml":      ["version", "hostname", "ssh", "firewall"],
    "audio.yaml":       ["version", "input", "output", "volume"],
    "asr.yaml":         ["version", "active", "models"],
    "tts.yaml":         ["version", "active_voice", "voices"],
    "llm.yaml":         ["version", "active_engine", "active_model", "models", "cpu", "hailo"],
    "wake-word.yaml":   ["version", "active", "models"],
    "voice-policy.yaml":["version", "allow_voice_mutation", "deny_voice_mutation"],
    "intents/system.yaml": ["version", "intents"],
    "vm/qemu-raspi.yaml":  ["version", "machine", "audio_backend"],
}


@pytest.mark.parametrize("rel,keys", list(EXPECTED.items()))
def test_config_loads(rel, keys):
    p = CFG_DIR / rel
    assert p.exists(), f"missing {p}"
    data = yaml.safe_load(p.read_text())
    for k in keys:
        assert k in data, f"{rel}: missing top-level {k!r}"


def test_llm_default_engine_is_safe():
    data = yaml.safe_load((CFG_DIR / "llm.yaml").read_text())
    assert data["active_engine"] in {"auto", "cpu"}, (
        "default engine must not be 'hailo' (the accelerator is optional)"
    )


def test_telemetry_off_by_default():
    data = yaml.safe_load((CFG_DIR / "system.yaml").read_text())
    assert data["telemetry"]["enabled"] is False
