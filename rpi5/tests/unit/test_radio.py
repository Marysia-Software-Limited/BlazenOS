"""Tier 0 — the internet-radio directory (resolves spoken names to streams).

Runs against the shipped ``configs/radio.yaml`` so it also guards the catalogue.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.radio import RadioDirectory

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _config_root(monkeypatch):
    monkeypatch.setenv("BLAZEN_CONFIG_ROOT", str(REPO / "configs"))


def test_loads_catalogue_with_krakow_and_trojka():
    d = RadioDirectory()
    assert d.available
    ids = {s.id for s in d.stations}
    assert {"trojka", "radio-krakow"} <= ids


def test_default_station_is_trojka():
    d = RadioDirectory()
    assert d.default_station() is not None and d.default_station().id == "trojka"


def test_resolve_is_accent_insensitive():
    d = RadioDirectory()
    assert d.resolve("włącz trójkę").id == "trojka"
    assert d.resolve("puść trojka").id == "trojka"


def test_resolve_radio_krakow():
    assert RadioDirectory().resolve("puść Radio Kraków").id == "radio-krakow"


def test_longest_alias_wins_off_radio_krakow():
    # "off radio kraków" must beat the shorter "radio kraków".
    assert RadioDirectory().resolve("włącz off radio kraków").id == "off-radio-krakow"


def test_bare_radio_resolves_to_none():
    assert RadioDirectory().resolve("włącz radio") is None


def test_every_station_has_a_url():
    for s in RadioDirectory().stations:
        assert s.url.startswith("http")


def test_offer_includes_headliners():
    names = [s.name for s in RadioDirectory().offer()]
    assert "Trójka" in names and "Radio Kraków" in names


def test_empty_directory_without_config(monkeypatch):
    monkeypatch.setenv("BLAZEN_CONFIG_ROOT", "/nonexistent-dir-xyz")
    d = RadioDirectory()
    assert not d.available and d.resolve("trójka") is None
