"""Tier 0 — every scenario YAML parses and has the required structure."""
from __future__ import annotations

from pathlib import Path

import yaml

SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"

REQUIRED_KEYS = {"id", "description", "turns"}


def test_scenarios_directory_present():
    assert SCENARIOS.is_dir()


def test_at_least_five_scenarios():
    files = list(SCENARIOS.glob("*.yaml"))
    assert len(files) >= 5, f"only found {len(files)} scenarios"


def test_scenarios_parse():
    for p in SCENARIOS.glob("*.yaml"):
        data = yaml.safe_load(p.read_text())
        missing = REQUIRED_KEYS - data.keys()
        assert not missing, f"{p.name}: missing keys {missing}"
        assert isinstance(data["turns"], list) and data["turns"], f"{p.name}: empty turns"


def test_scenario_ids_unique():
    ids = []
    for p in SCENARIOS.glob("*.yaml"):
        ids.append(yaml.safe_load(p.read_text())["id"])
    assert len(set(ids)) == len(ids), f"duplicate scenario ids: {ids}"


def test_each_turn_has_user_or_inject():
    for p in SCENARIOS.glob("*.yaml"):
        data = yaml.safe_load(p.read_text())
        for i, turn in enumerate(data["turns"]):
            assert "user" in turn or "inject" in turn, (
                f"{p.name} turn {i}: needs `user:` or `inject:`"
            )
