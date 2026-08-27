"""Weather adapter — current conditions via Open-Meteo (keyless, on-device).

The "jaka pogoda" / "what's the weather" intent. Open-Meteo needs **no API
key** and is a plain HTTP+JSON service (not a cloud LLM), so it fits the
on-device contract better than routing weather through Gemini. Default location
is **Kraków** (Polish-first, per ``configs/weather.yaml``); other cities are
resolved by name via Open-Meteo's geocoding endpoint.

No SDK — just ``urllib`` — and the HTTP ``transport`` is injectable so tests run
fully offline and deterministically (mirrors :mod:`blazend.domains.ai_orchestrator.adapters.rpi5.assistant.gemini`).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from blazend.config import load
from blazend.i18n import msg, table

_FORECAST = (
    "https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
    "&current=temperature_2m,apparent_temperature,weather_code,wind_speed_10m"
    "&daily=precipitation_probability_max,temperature_2m_max,temperature_2m_min,weather_code"
    "&forecast_days=1&wind_speed_unit={wind}&temperature_unit={temp}&timezone=auto"
)
# Rain-focused query: daily max probability across `days`, plus an hourly
# probability track so Jessica can say WHEN it will rain ("koło 15:00"). `current`
# is fetched only for its `time` (to locate "now" in the hourly arrays — no local
# clock math). timezone=auto so hourly times are local.
_RAIN = (
    "https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
    "&current=temperature_2m"
    "&hourly=precipitation_probability"
    "&daily=precipitation_probability_max"
    "&forecast_days={days}&timezone=auto"
)
_GEOCODE = "https://geocoding-api.open-meteo.com/v1/search?name={q}&count=1&language={lang}&format=json"

# A transport takes a URL and returns parsed JSON (GET only — Open-Meteo).
Transport = Callable[[str], dict[str, Any]]

def describe_code(code: int, lang: str) -> str:
    """WMO weather-code → short spoken description, from the message catalog
    (``tables.weather_codes``; unknown codes → ``weather.unknown_conditions``)."""
    return table("weather_codes", code, lang,
                 default=msg("weather.unknown_conditions", lang))


class WeatherError(RuntimeError):
    """Raised when a weather lookup cannot be performed."""


@dataclass
class Conditions:
    """A resolved current-weather snapshot plus today's forecast (rain + range)."""

    place: str
    temperature: float
    feels_like: float
    wind_speed: float
    code: int
    units_temp: str = "°C"
    units_wind: str = "km/h"
    rain_prob: int | None = None   # today's max precipitation probability, %
    temp_max: float | None = None  # today's high
    temp_min: float | None = None  # today's low


@dataclass
class RainOutlook:
    """A rain-focused forecast: today's + tomorrow's max chance, and when it peaks.

    ``next_hours`` is ``[(local_hour, prob_pct)]`` over the coming window; ``peak_*``
    is the highest-probability hour in that window (``None`` when no hourly data or
    the window is dry). ``today_max`` / ``tomorrow_max`` are the daily maxima."""

    place: str
    today_max: int | None = None
    tomorrow_max: int | None = None
    next_hours: list[tuple[int, int]] = field(default_factory=list)
    peak_hour: int | None = None
    peak_prob: int | None = None


@dataclass
class Place:
    name: str
    latitude: float
    longitude: float


def _http_get(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 (fixed host)
            result: dict[str, Any] = json.loads(resp.read().decode("utf-8"))
            return result
    except urllib.error.HTTPError as e:  # pragma: no cover - network path
        raise WeatherError(f"weather HTTP {e.code}") from e
    except urllib.error.URLError as e:  # pragma: no cover - network path
        raise WeatherError(f"weather service unreachable: {e.reason}") from e


class WeatherClient:
    """Thin Open-Meteo client. Default location + units from ``weather.yaml``."""

    def __init__(self, *, config_name: str = "weather", transport: Transport | None = None) -> None:
        self._transport = transport or _http_get
        # Load defaults defensively — works even with no config root (tests).
        name, lat, lon, units, geo = "Kraków", 50.0614, 19.9366, "metric", True
        days, window, peak = 2, 8, 40
        try:
            cfg = load(config_name)
            loc = cfg.get("default_location", {}) or {}
            name = str(loc.get("name", name))
            lat = float(loc.get("latitude", lat))
            lon = float(loc.get("longitude", lon))
            units = str(cfg.get("units", units))
            geo = bool(cfg.get("allow_geocoding", geo))
            days = int(cfg.get("forecast_days", days))
            window = int(cfg.get("hourly_window_h", window))
            peak = int(cfg.get("rain_peak_threshold", peak))
        except Exception:  # noqa: BLE001 — missing/unreadable config → Kraków defaults
            pass
        self.default_place = Place(name, lat, lon)
        self._metric = units != "imperial"
        self._allow_geocoding = geo
        self._forecast_days = max(1, min(days, 7))       # Open-Meteo daily cap
        self._hourly_window = max(1, min(window, 24))
        self._peak_threshold = peak                       # below this, "kiedy" says "raczej sucho"

    @property
    def available(self) -> bool:
        return True  # keyless; only the network itself can fail (raised at call)

    def geocode(self, place: str, lang: str = "pl") -> Place | None:
        """Resolve a city name → coordinates (or ``None`` if not found/disabled)."""
        if not self._allow_geocoding or not place.strip():
            return None
        url = _GEOCODE.format(q=urllib.parse.quote(place.strip()), lang=lang)
        data = self._transport(url)
        results = data.get("results") or []
        if not results:
            return None
        top = results[0]
        return Place(str(top.get("name", place)), float(top["latitude"]), float(top["longitude"]))

    def current(self, place: Place | None = None) -> Conditions:
        """Fetch current conditions for ``place`` (default location if None)."""
        p = place or self.default_place
        temp_unit = "celsius" if self._metric else "fahrenheit"
        wind_unit = "kmh" if self._metric else "mph"
        url = _FORECAST.format(lat=p.latitude, lon=p.longitude, wind=wind_unit, temp=temp_unit)
        data = self._transport(url)
        cur = data.get("current") or {}
        daily = data.get("daily") or {}

        def _first(key: str) -> float | None:
            seq = daily.get(key) or []
            try:
                return float(seq[0]) if seq else None
            except (TypeError, ValueError):
                return None

        rain = _first("precipitation_probability_max")
        try:
            return Conditions(
                place=p.name,
                temperature=float(cur["temperature_2m"]),
                feels_like=float(cur.get("apparent_temperature", cur["temperature_2m"])),
                wind_speed=float(cur.get("wind_speed_10m", 0.0)),
                code=int(cur.get("weather_code", -1)),
                units_temp="°C" if self._metric else "°F",
                units_wind="km/h" if self._metric else "mph",
                rain_prob=int(rain) if rain is not None else None,
                temp_max=_first("temperature_2m_max"),
                temp_min=_first("temperature_2m_min"),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise WeatherError(f"unexpected weather response: {data!r}"[:200]) from e

    def rain(self, place: Place | None = None) -> RainOutlook:
        """Rain-focused outlook for ``place``: today's + tomorrow's max chance, and
        the peak hour over the coming ``hourly_window_h`` (so Jessica can say WHEN)."""
        p = place or self.default_place
        url = _RAIN.format(lat=p.latitude, lon=p.longitude, days=self._forecast_days)
        data = self._transport(url)
        cur = data.get("current") or {}
        daily = data.get("daily") or {}
        hourly = data.get("hourly") or {}

        def _daymax(i: int) -> int | None:
            seq = daily.get("precipitation_probability_max") or []
            try:
                return int(seq[i]) if i < len(seq) and seq[i] is not None else None
            except (TypeError, ValueError):
                return None

        # Locate "now" in the hourly track by matching the current hour (string
        # compare on "YYYY-MM-DDTHH" — no local clock arithmetic).
        times = hourly.get("time") or []
        probs = hourly.get("precipitation_probability") or []
        now_key = str(cur.get("time", ""))[:13]
        start = next((i for i, t in enumerate(times) if str(t)[:13] >= now_key), 0) if now_key else 0

        next_hours: list[tuple[int, int]] = []
        for t, pr in zip(times[start:start + self._hourly_window],
                         probs[start:start + self._hourly_window], strict=False):
            if pr is None:
                continue
            try:
                next_hours.append((int(str(t)[11:13]), int(pr)))
            except (TypeError, ValueError):
                continue

        peak_hour = peak_prob = None
        if next_hours:
            peak_hour, peak_prob = max(next_hours, key=lambda hp: hp[1])

        return RainOutlook(
            place=p.name,
            today_max=_daymax(0),
            tomorrow_max=_daymax(1),
            next_hours=next_hours,
            peak_hour=peak_hour,
            peak_prob=peak_prob,
        )


__all__ = [
    "WeatherClient",
    "Conditions",
    "RainOutlook",
    "Place",
    "WeatherError",
    "describe_code",
]
