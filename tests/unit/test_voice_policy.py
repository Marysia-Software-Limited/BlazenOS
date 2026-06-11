"""Tier 0 — voice-policy.yaml is internally consistent."""
from __future__ import annotations

from pathlib import Path

import yaml

CFG = Path(__file__).resolve().parents[2] / "configs" / "voice-policy.yaml"

VALID_CONFIRM = {"never", "single", "loud", "double_loud", "timed_window"}


def test_loads():
    yaml.safe_load(CFG.read_text())


def test_every_allow_has_known_confirm():
    data = yaml.safe_load(CFG.read_text())
    for k, v in data.get("allow_voice_mutation", {}).items():
        c = (v or {}).get("confirm")
        assert c in VALID_CONFIRM, f"{k}: bad confirm {c!r}"


def test_destructive_keys_require_loud_or_stronger():
    data = yaml.safe_load(CFG.read_text())
    allow = data.get("allow_voice_mutation", {})
    for k in ("system.power.reboot", "system.power.shutdown", "ssh.enabled", "telemetry.enabled"):
        assert k in allow, f"{k} missing from allow_voice_mutation"
        assert allow[k]["confirm"] in {"loud", "double_loud"}, f"{k} confirm too weak"
    assert allow["system.factory_reset"]["confirm"] == "double_loud"


def test_secret_paths_denied():
    data = yaml.safe_load(CFG.read_text())
    deny = data.get("deny_voice_mutation", [])
    assert any("secret" in d for d in deny), "no deny rule for secret paths"
    assert any("firewall" in d for d in deny), "no deny rule for firewall paths"
