# Workplan — News · Weather · Rain forecast

**Date:** 2026-07-11 · **Surface:** Pi 5 (`rpi5/`), shared engine
(`ai_orchestrator`) · **Owner:** paul (dev) → deploy to jessica.
**Focus (user):** **rain forecast is the star.** Weather is only touched where
it serves rain; general-weather polish is explicitly de-prioritized. News second.

## Guardrails (unchanged invariants)
- **On-device is the floor.** Weather (Open-Meteo) and RSS news are keyless
  HTTP+JSON/XML — not cloud LLMs — so they satisfy the on-device contract.
  OpenAI/Gemini for news are **strict-improvement, opt-in** (key in
  `/etc/blazen/secrets.env`); every path must degrade to the keyless floor.
- **PL leads; PL+EN asset parity.** Runtime is Polish-only, but every new
  intent trigger, phrase, and scenario ships a `pl:` and `en:` counterpart.
- **Voice-first.** Every answer must be short enough to *hear*; rain timing
  and probability lead — a blind user asking "czy będzie padać?" wants the
  number and the hour, not a conditions dump.
- **Deploy discipline.** After any Pi push, restart **`blazend.target`** (whole
  pipeline) — subscribers don't reconnect to a restarted publisher.

---

## Current state (what already works)
- `assistant/weather.py` — Open-Meteo client: current conditions **+ today's**
  `precipitation_probability_max`, `temperature_2m_{max,min}`. Kraków default,
  geocoding for named cities. Injectable transport (offline tests).
- `tools.weather_now()` — leads with `Szansa opadów N%.`, then now-temp + desc +
  today's range + feels/wind. Live-verified (Kraków 76%).
- `tools.news_brief()` — OpenAI search model (`gpt-4o-search-preview`) →
  Gemini grounded → RSS. `_clean_spoken()` strips URLs/citations.
- Intents `weather_query` (pogoda / deszcz / opad / śnieg / temperatura / rain /
  snow, with `<place>` capture) and `news_brief`.

## Gaps this plan closes
1. Rain is **today-only** — "czy będzie padać **jutro**?" / "**kiedy dziś**?"
   unanswerable. No hourly timing ("koło 15:00"), no multi-day.
2. No **rain-specific** reply shape — a rain question gets the full weather dump.
3. No dedicated **"nie mam dostępu do prognozy"** message when *rain data
   specifically* is missing (user's explicit ask).
4. News **re-hits the cloud every time** (cost + latency); no short cache.
5. RSS **floor is thin** (2 feeds/lang, no dedup/recency) — the on-device path
   should be solid, not a token fallback.
6. Weather/rain/news have **no scenario coverage** (`rpi5/tests/scenarios/`).

---

## Phase R — Rain forecast (priority) — ✅ SHIPPED 2026-07-11

> Done: `weather.py rain()` + `RainOutlook` (hourly + 2-day, peak hour);
> `tools.rain_forecast()` (rain-first reply, dedicated "Nie mam dostępu do prognozy
> opadów." on missing data); `rain_forecast` intent before `weather_query` with
> `<place>`/`<when>`; config knobs (W1). Deployed + live-verified on the Pi.
> Original spec below.


### R1. Hourly + multi-day data in `weather.py`
- Extend `_FORECAST`: add `hourly=precipitation_probability,precipitation` and
  raise `forecast_days=2` (today + tomorrow). Keep the existing `daily=` block.
- New `RainOutlook` dataclass: `today_max:int|None`, `tomorrow_max:int|None`,
  `next_hours:list[(hour:int, prob:int)]` (next ~6–8 h), `peak_hour:int|None`
  (the hour of highest prob in the window, when it clears a threshold).
- `WeatherClient.rain(place, when="auto"|"today"|"tomorrow")` → `RainOutlook`,
  parsing the hourly arrays around "now" (use the API's `timezone=auto` + the
  `time` array; no local clock math beyond index-of-now).
- **DoD:** unit tests with a fixed hourly-JSON fixture assert peak-hour and the
  today/tomorrow maxima; offline (injected transport).

### R2. Rain-specific reply + dedicated unavailable message (`tools.py`)
- `Tools.rain_forecast(place_name, when, lang)` → `ToolResult`. Reply shape,
  rain **first**, timing when known:
  - PL: `"Szansa opadów dziś {N}%{, koło {H}:00}. Jutro {M}%."`
  - EN: `"Chance of rain today {N}%{, around {H}:00}. Tomorrow {M}%."`
  - When `when="tomorrow"`, lead with tomorrow.
- **Unavailable message (explicit user ask):** if the rain fields are missing
  (API returned no precipitation data), return
  `_t(lang, "Nie mam dostępu do prognozy opadów.", "I don't have access to the
  rain forecast.")` with `success=False` — distinct from the generic weather
  error.
- **DoD:** `test_tools.py` covers: rain today+tomorrow, "kiedy będzie padać"
  → peak hour, missing-data → the no-access message. PL+EN.

### R3. Rain intent + routing
- New intent `rain_forecast` **before** `weather_query` in
  `configs/intents/system.yaml` (so a rain question doesn't fall into the full
  weather dump). Triggers, PL+EN, capturing `<place>` and a `<when>` hint:
  - PL: `czy (będzie|dziś|jutro) pad\w*`, `kiedy (będzie )?pad\w*`,
    `deszcz\w*`, `opad\w*`, `czy wziąć parasol`.
  - EN: `will it rain`, `is it going to rain`, `when.*rain`, `do I need an
    umbrella`.
  - `<when>`: `jutro`/`tomorrow` → `when="tomorrow"`, else `auto`.
- Wire `rain.forecast` → `Tools.rain_forecast` in `dispatch.py` `_TOOL_INTENTS`.
- Keep `weather_query` for general "jaka pogoda" (unchanged).
- **DoD:** `test_intent_triggers.py` — "czy będzie jutro padać w Warszawie"
  → `rain_forecast`, place=Warszawa, when=tomorrow; "jaka pogoda" still
  `weather_query`. Ambient sentences don't match (anchored/​wake-gated).

---

## Phase W — Weather config (only what rain needs)

General-weather features (tomorrow's temps, "jaka pogoda jutro", conditions
polish) are **out of scope for this cut** — `weather_now` stays as-is. The only
weather work here is the config knobs Phase R depends on.

### W1. Config for the rain window
- `configs/weather.yaml`: `forecast_days` (2), `hourly_window_h` (default 8),
  `rain_peak_threshold` (default 40 %, below which "kiedy" says "raczej bez
  opadów"). Doc entry in `docs/07-CONFIGURATION.md`.
- *(Deferred, not in this cut:* tomorrow's temperature range, general-conditions
  rewording — revisit only after rain + news ship.*)*

---

## Phase N — News robustness

### N1. Short cache
- Cache the last successful `news_brief` per `lang` for a TTL (config
  `news.yaml: cache_minutes: 15`). Re-ask inside the window replays the cache —
  no cloud round-trip, instant. Cache is in-memory on the orchestrator (lost on
  restart, which is fine).
- **DoD:** test: two calls in-window hit the client once (injected fake).

### N2. Strengthen the RSS floor
- `news.yaml`: add 1–2 more reputable feeds per lang; dedup near-identical
  headlines (title similarity), prefer most recent, cap at `max_headlines`.
- **DoD:** `news.headlines()` test with a multi-feed fixture asserts dedup +
  recency ordering. Verify the keyless path answers with the cloud key removed.

### N3. (Stretch) Category split
- Optional `<topic>` capture (sport / Polska / świat / biznes) mapped to the
  OpenAI query and RSS feed subset. Defer if it bloats the intent.

---

## Phase B — Convergence (stretch, after R/W/N)
`docs/product/07-DAILY-BRIEFING.md` already specs a morning brief whose
`weather` + `news_*` sections are exactly R/W/N. Once those land, wire the
`weather` (with rain-first) and `news_local/national/world` sections into the
briefing builder. Out of scope for the first cut; noted so R/W/N shapes stay
briefing-compatible (short, section-budgeted).

---

## Verification (every phase)
- `make test-fast` green (ruff + mypy + rustfmt + clippy, then Tier 0/1).
- New scenarios in `rpi5/tests/scenarios/` (PL + EN): `rain_today`,
  `rain_tomorrow`, `weather_krakow`, `news_brief` — assert the spoken text
  leads with rain %/timing and news returns ≤3 short items.
- Config change ⇒ default in `configs/` **and** a `docs/07-CONFIGURATION.md`
  entry (checklist §8).
- **On-device proof:** pull the OpenAI/Gemini keys → weather, rain, and RSS
  news still answer. Then restore keys → cloud news improves. Toggle proves
  the floor holds.
- **Pi deploy:** scp to `/usr/lib/blazen/`, install configs to `/etc/blazen/`,
  clear `__pycache__`, restart `blazend.target`. Voice-test:
  "czy będzie dziś padać?", "a jutro?", "jaka pogoda w Gdańsku",
  "co w wiadomościach?".

## Suggested order
**Rain is the priority and ships first:** R1 → R2 → R3 (with the one config knob
W1 folded in). Then news: N1 → N2 → (N3/B stretch). General-weather polish stays
deferred. Each of R and N is independently shippable and testable.
