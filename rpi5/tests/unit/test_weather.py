"""Tier 0 — the Open-Meteo weather client (offline; injected HTTP transport)."""
from __future__ import annotations

from blazend.assistant.weather import Place, WeatherClient, describe_code


def _client(*, forecast=None, geo=None):
    def transport(url):
        if "geocoding-api" in url:
            return {"results": geo if geo is not None else []}
        return {"current": forecast or {
            "temperature_2m": 12.5, "apparent_temperature": 11.0,
            "weather_code": 61, "wind_speed_10m": 8.0,
        }}
    return WeatherClient(transport=transport)


def test_default_location_is_krakow():
    c = WeatherClient(transport=lambda url: {"current": {}})
    assert c.default_place.name == "Kraków"
    assert round(c.default_place.latitude, 1) == 50.1


def test_current_parses_open_meteo_payload():
    cond = _client().current()
    assert cond.place == "Kraków"
    assert cond.temperature == 12.5 and cond.feels_like == 11.0
    assert cond.wind_speed == 8.0 and cond.code == 61
    assert cond.units_temp == "°C" and cond.units_wind == "km/h"


def test_current_uses_passed_place():
    cond = _client().current(Place("London", 51.5, -0.12))
    assert cond.place == "London"


def test_geocode_resolves_named_city():
    c = _client(geo=[{"name": "Warszawa", "latitude": 52.23, "longitude": 21.01}])
    place = c.geocode("Warszawa")
    assert place is not None and place.name == "Warszawa"
    assert round(place.latitude, 1) == 52.2


def test_geocode_returns_none_when_not_found():
    assert _client(geo=[]).geocode("Xyzzy") is None


def test_describe_code_is_bilingual():
    assert describe_code(0, "pl") == "bezchmurnie"
    assert describe_code(0, "en") == "clear sky"
    assert describe_code(95, "pl") == "burza"
    assert "unknown" in describe_code(123, "en")


def test_imperial_units_when_configured(monkeypatch, tmp_path):
    # A weather.yaml with imperial units flips the unit labels.
    (tmp_path / "weather.yaml").write_text(
        "version: 1\nunits: imperial\ndefault_location:\n"
        "  name: Kraków\n  latitude: 50.06\n  longitude: 19.94\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("BLAZEN_CONFIG_ROOT", str(tmp_path))
    c = _client(forecast={"temperature_2m": 60.0, "apparent_temperature": 58.0,
                          "weather_code": 0, "wind_speed_10m": 5.0})
    cond = c.current()
    assert cond.units_temp == "°F" and cond.units_wind == "mph"
