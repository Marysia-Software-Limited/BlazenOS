"""Unit tests for the Phase 4d tool services + tool server.

Behaviour is ported from the old engine tool methods; these lock the spoken
phrasing and the tool.response shape with injected fakes (no network).
"""

from __future__ import annotations

from typing import Any

from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.weather import WeatherClient
from blazend.domains.ai_orchestrator.adapters.rpi5.tool_server import ToolService
from blazend.domains.ai_orchestrator.adapters.rpi5.tools import Tools
from blazend.events import TOPIC_TOOL_REQUEST, TOPIC_TOOL_RESPONSE, Envelope


class _Note:
    def __init__(self, text: str) -> None:
        self.text = text
        self.id = "note-1"
        self.title = ""


class _FakeMemory:
    def __init__(self, notes: list[str]) -> None:
        self._notes = [_Note(t) for t in notes]
        self.profile: dict[str, str] = {}
        self.added: list[str] = []

    def notes(self) -> list[_Note]:
        return self._notes

    def pending(self) -> list[Any]:
        return []

    def add_note(self, text: str, *, now: Any, title: str = "") -> _Note:
        self.added.append(text)
        n = _Note(text)
        n.id = f"note-{len(self.added)}"  # type: ignore[attr-defined]
        return n

    def set_profile(self, key: str, value: str, *, now: Any) -> None:
        self.profile[key] = value

    def claim_last_clip(self, utterance_text: str, *, max_age_s: float = 15.0) -> None:
        return None  # no ASR clip in unit tests → plain text-note path

    def memory_items(self) -> list[Any]:
        from blazend.domains.context.adapters.rpi5.memory import MemoryItem
        return [MemoryItem(id=n.id, kind="note", text=n.text, title=n.title)
                for n in self._notes]

    def voice_notes(self) -> list[Any]:
        return []


class _Station:
    def __init__(self, sid: str, name: str, url: str) -> None:
        self.id, self.name, self.url = sid, name, url


class _FakeRadio:
    available = True

    def __init__(self, station: _Station | None) -> None:
        self._station = station
        self._all = [_Station("trojka", "Trójka", "http://x"), _Station("rmf", "RMF", "http://y")]

    def resolve(self, text: str) -> _Station | None:
        return self._station

    def offer(self, limit: int = 4) -> list[_Station]:
        return self._all


def _tools(**kw: Any) -> Tools:
    return Tools(**kw)  # type: ignore[arg-type]


def test_recall_notes_lists_recent() -> None:
    t = _tools(memory=_FakeMemory(["kup mleko", "oddać książkę"]))
    res = t.recall_notes("pl")
    assert res.ok
    assert "Pamiętam: kup mleko; oddać książkę." == res.text


def test_recall_notes_empty() -> None:
    t = _tools(memory=_FakeMemory([]))
    assert t.recall_notes("en").text == "I haven't noted anything yet."


def test_radio_play_named_station() -> None:
    t = _tools(radio=_FakeRadio(_Station("trojka", "Trójka", "http://x")))
    res = t.radio_play("trójka", "pl")
    assert res.action == "radio_play"
    assert res.text == "Włączam stację Trójka."
    assert res.payload["url"] == "http://x"


def test_radio_play_offers_when_unresolved() -> None:
    t = _tools(radio=_FakeRadio(None))
    res = t.radio_play("coś", "pl")
    assert res.action == "radio_offer"
    assert "Trójka" in res.text and "RMF" in res.text


def test_radio_stop() -> None:
    t = _tools(radio=_FakeRadio(None))
    assert t.radio_stop("en").text == "Turning off the radio."


def test_remember_stores_note() -> None:
    mem = _FakeMemory([])
    t = _tools(memory=mem)
    res = t.remember("kup mleko", "pl")
    assert res.ok and res.action == "note"
    assert mem.added == ["kup mleko"]
    assert "Zapamiętałam: kup mleko." == res.text


def test_remember_empty_asks_what() -> None:
    res = _tools(memory=_FakeMemory([])).remember("  ", "en")
    assert res.text == "What should I remember?"


def test_set_name_capitalises_and_stores() -> None:
    mem = _FakeMemory([])
    res = _tools(memory=mem).set_name("paweł", "pl")
    assert mem.profile["name"] == "Paweł"
    assert "Paweł" in res.text and res.action == "profile"


def test_run_routes_memory_writes() -> None:
    mem = _FakeMemory([])
    t = _tools(memory=mem)
    t.run("context.remember", {"text": "oddać książkę"}, "pl")
    t.run("context.set_name", {"name": "Ala"}, "pl")
    assert mem.added == ["oddać książkę"]
    assert mem.profile["name"] == "Ala"


class _RecMemory(_FakeMemory):
    def __init__(self) -> None:
        super().__init__([])
        self.reminders: list[dict[str, Any]] = []

    def add_reminder(self, text: str, *, due: Any, now: Any, category: str = "reminder") -> Any:
        self.reminders.append({"text": text, "due": due, "category": category})
        r = _Note(text)
        r.id = "rem-1"  # type: ignore[attr-defined]
        r.due = due.isoformat()  # type: ignore[attr-defined]
        return r


def test_add_reminder_parses_time_and_task() -> None:
    mem = _RecMemory()
    res = _tools(memory=mem).add_reminder("o spotkaniu o 15:00", "pl")
    assert res.action == "reminder"
    assert len(mem.reminders) == 1
    assert mem.reminders[0]["due"].hour == 15
    assert "spotkaniu" in mem.reminders[0]["text"]


def test_add_reminder_without_time_asks_when() -> None:
    res = _tools(memory=_RecMemory()).add_reminder("o spotkaniu", "pl")
    assert res.action == "wake"
    assert "kiedy" in res.text.lower()


def test_run_unknown_tool() -> None:
    t = _tools(memory=_FakeMemory([]))
    res = t.run("does.not.exist", {}, "en")
    assert not res.ok and res.action == "error"


def test_tool_service_builds_response_envelope() -> None:
    svc = ToolService(tools=_tools(radio=_FakeRadio(_Station("rmf", "RMF", "http://y"))))
    req = Envelope(
        topic=TOPIC_TOOL_REQUEST,
        source="blazend-mind",
        data={"request_id": "t-9", "tool": "radio.play", "language": "pl", "args": {"query": "rmf"}},
    )
    resp = svc.response_for(req)
    assert resp.topic == TOPIC_TOOL_RESPONSE
    assert resp.data["request_id"] == "t-9"
    assert resp.data["ok"] is True
    assert resp.data["action"] == "radio_play"
    assert resp.data["payload"]["name"] == "RMF"


# -- new: weather with rain probability, OpenAI news, audiobook menu ----------

def _weather(daily: dict) -> WeatherClient:
    def transport(url: str) -> dict:
        return {
            "current": {"temperature_2m": 15.0, "apparent_temperature": 14.0,
                        "weather_code": 3, "wind_speed_10m": 10.0},
            "daily": daily,
        }
    return WeatherClient(transport=transport)


def test_weather_reports_rain_probability_and_range() -> None:
    wc = _weather({"precipitation_probability_max": [70],
                   "temperature_2m_max": [24.0], "temperature_2m_min": [15.0]})
    res = _tools(weather=wc).weather_now(None, "pl")
    assert res.ok
    assert "Szansa opadów 70%" in res.text        # rain probability, spoken first
    assert "od 15 do 24" in res.text              # today's range
    assert res.payload.get("rain_prob") == 70


def test_weather_omits_rain_when_absent() -> None:
    # No daily block (older payload) → no rain sentence, still answers.
    res = _tools(weather=_weather({})).weather_now(None, "pl")
    assert res.ok and "Szansa opadów" not in res.text


def test_weather_focuses_on_rain_probability() -> None:
    # The weather answer LEADS with rain and drops wind / feels-like so the
    # probability isn't buried.
    wc = _weather({"precipitation_probability_max": [70],
                   "temperature_2m_max": [24.0], "temperature_2m_min": [15.0]})
    res = _tools(weather=wc).weather_now(None, "pl")
    assert res.text.index("Szansa opadów 70%") < res.text.index("Teraz")  # rain first
    assert "wiatr" not in res.text and "Odczuwalna" not in res.text         # trimmed


def _rain_weather(*, days_max=(76, 40), now="2026-07-11T11:00", hourly=None) -> WeatherClient:
    times = ["2026-07-11T11:00", "2026-07-11T12:00", "2026-07-11T13:00"]
    probs = [30, 80, 50]
    if hourly is not None:
        times, probs = hourly

    def transport(url: str) -> dict:
        return {
            "current": {"time": now, "temperature_2m": 19.0},
            "daily": {"precipitation_probability_max": list(days_max)},
            "hourly": {"time": times, "precipitation_probability": probs},
        }
    return WeatherClient(transport=transport)


def test_rain_forecast_leads_with_chance_then_peak_then_tomorrow() -> None:
    res = _tools(weather=_rain_weather()).rain_forecast(None, "", "pl")
    assert res.ok and res.action == "rain"
    assert res.text.startswith("Szansa opadów dziś 76%.")
    assert "Najwięcej koło 12:00." in res.text      # peak hour (80% ≥ threshold)
    assert "Jutro 40%." in res.text


def test_rain_forecast_tomorrow_when_asked() -> None:
    res = _tools(weather=_rain_weather()).rain_forecast(None, "jutro", "pl")
    assert res.ok and res.payload.get("when") == "tomorrow"
    assert res.text == "Jutro szansa opadów 40%."


def test_rain_forecast_omits_peak_below_threshold() -> None:
    # All hours under the 40% peak threshold → no "najwięcej koło" clause.
    calm = (["2026-07-11T11:00", "2026-07-11T12:00"], [10, 20])
    res = _tools(weather=_rain_weather(days_max=(20, 15), hourly=calm)).rain_forecast(None, "", "pl")
    assert res.ok and "Najwięcej" not in res.text
    assert res.text.startswith("Szansa opadów dziś 20%.")


def test_rain_forecast_no_access_when_data_missing() -> None:
    # The explicit user ask: no rain data → say she has no access (not a temp dump).
    res = _tools(weather=_rain_weather(days_max=())).rain_forecast(None, "", "pl")
    assert not res.ok and res.payload.get("reason") == "rain_unavailable"
    assert res.text == "Nie mam dostępu do prognozy opadów."


def test_rain_forecast_english() -> None:
    res = _tools(weather=_rain_weather()).rain_forecast(None, "", "en")
    assert res.text.startswith("Chance of rain today 76%.")
    assert "Highest around 12:00." in res.text


class _FakeOpenAi:
    def __init__(self, text: str = "", available: bool = True) -> None:
        self._text, self._available, self.calls = text, available, []

    @property
    def available(self) -> bool:
        return self._available

    def chat(self, user: str, *, system: str | None = None) -> str:
        self.calls.append((user, system))
        return self._text


class _FakeNews:
    """Offline tiered news client — canned headlines per tier, no network."""

    def __init__(self, tiers: dict[str, list[str]] | None = None) -> None:
        self._tiers = tiers or {
            "local": ["Kraków: remont Wawelu"],
            "national": ["Sejm uchwalił budżet"],
            "world": ["UN summit opens in Geneva"],  # English → composer translates
            "world_pl": ["Szczyt ONZ w Genewie"],
        }

    def by_tier(self, tier: str, limit: int | None = None) -> list[str]:
        return list(self._tiers.get(tier, []))

    def collect(self, tiers, limit=None) -> dict[str, list[str]]:
        return {t: self.by_tier(t) for t in tiers}


def test_news_uses_openai_when_key_present() -> None:
    fake = _FakeOpenAi("Z Krakowa: remont. Z kraju: budżet. Ze świata: szczyt ONZ.")
    res = _tools(openai=fake, news=_FakeNews()).news_brief("pl")
    assert res.ok and res.payload.get("source") == "openai"
    assert "Z Krakowa" in res.text
    assert fake.calls  # OpenAI was actually called
    # The composer was handed the real headlines to translate/summarise.
    user_prompt = fake.calls[0][0]
    assert "UN summit" in user_prompt and "remont Wawelu" in user_prompt


def test_news_strips_urls_from_openai() -> None:
    fake = _FakeOpenAi("Nagłówek [źródło](https://x.com/a). Drugi https://y.com koniec.")
    res = _tools(openai=fake, news=_FakeNews()).news_brief("pl")
    assert "https://" not in res.text and "źródło" in res.text


def test_news_strips_markdown_for_tts() -> None:
    # A model that answers with Markdown must not read `**`/`#` aloud.
    fake = _FakeOpenAi("**Z Krakowa:**\n# Nagłówek\nTreść *ważna* tutaj.")
    res = _tools(openai=fake, news=_FakeNews()).news_brief("pl")
    assert "*" not in res.text and "#" not in res.text
    assert "\n" not in res.text  # one spoken line
    assert "Z Krakowa:" in res.text and "ważna" in res.text


def test_news_floor_speaks_polish_tiers_without_cloud() -> None:
    # No OpenAI, no Gemini → keyless floor reads the Polish tiers, world in Polish.
    fake = _FakeOpenAi(available=False)
    res = _tools(openai=fake, news=_FakeNews()).news_brief("pl")
    assert res.ok and res.payload.get("source") == "rss"
    assert "Z Krakowa: Kraków: remont Wawelu." in res.text
    assert "Szczyt ONZ w Genewie" in res.text  # world_pl, not the English headline
    assert "UN summit" not in res.text


class _FakeBook:
    def __init__(self, title: str) -> None:
        self.title = title


class _FakeBooks:
    def __init__(self, titles: list[str]) -> None:
        self.books = [_FakeBook(t) for t in titles]

    @property
    def available(self) -> bool:
        return bool(self.books)

    def offer(self, limit: int = 6) -> list[_FakeBook]:
        return self.books[:limit]


def test_audiobook_list_speaks_menu() -> None:
    t = _tools()
    t.audiobooks = _FakeBooks(["Metro 2033", "Alicja w Krainie Czarów", "Anna Karenina"])  # type: ignore[assignment]
    res = t.audiobook_list("pl")
    assert res.ok and res.payload.get("count") == 3
    assert "Metro 2033" in res.text and "Anna Karenina" in res.text
    assert "przeczytaj" in res.text.lower()


def test_audiobook_list_empty() -> None:
    t = _tools()
    t.audiobooks = _FakeBooks([])  # type: ignore[assignment]
    res = t.audiobook_list("pl")
    assert res.ok and "Nie mam jeszcze" in res.text


class _FakeResearchOpenAi(_FakeOpenAi):
    """Fake with the Responses-API research surface (web_search)."""

    def __init__(self, research_text: str = "", *, fail: bool = False, **kw) -> None:
        super().__init__(**kw)
        self._research_text, self._fail, self.research_calls = research_text, fail, []

    def research(self, user: str, *, system: str | None = None) -> str:
        self.research_calls.append((user, system))
        if self._fail:
            from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.openai import OpenAiError
            raise OpenAiError("web_search unavailable")
        return self._research_text


def test_news_prefers_live_web_research() -> None:
    fake = _FakeResearchOpenAi("Z Krakowa: mecz. Z kraju: budżet. Ze świata: szczyt.")
    res = _tools(openai=fake, news=_FakeNews()).news_brief("pl")
    assert res.ok and res.payload.get("source") == "openai-web"
    assert fake.research_calls and not fake.calls  # research used, chat not needed
    q = fake.research_calls[0][0]
    assert "Z Krakowa" in q and "Z kraju" in q and "Ze świata" in q


def test_news_research_failure_falls_back_to_rss_compose() -> None:
    fake = _FakeResearchOpenAi(fail=True, text="Z Krakowa: remont. Z kraju: budżet. Ze świata: ONZ.")
    res = _tools(openai=fake, news=_FakeNews()).news_brief("pl")
    assert res.ok and res.payload.get("source") == "openai"  # RSS+compose ladder held
    assert fake.research_calls and fake.calls


def test_sport_brief_uses_web_research_football_first() -> None:
    fake = _FakeResearchOpenAi("Z Krakowa: Wisła wygrała. Z kraju: kadra. Ze świata: finał LM.")
    res = _tools(openai=fake, news=_FakeNews()).news_sport("pl")
    assert res.ok and res.payload.get("source") == "openai-web"
    assert res.payload.get("kind") == "sport"
    q = fake.research_calls[0][0]
    assert "piłka nożna" in q and "Wisła" in q and "Ekstraklasa" in q


def test_sport_brief_floor_reads_sport_tier() -> None:
    fake = _FakeOpenAi(available=False)  # no key → keyless floor
    news = _FakeNews({"sport": ["Wisła Kraków wygrała derby", "Świątek w finale"]})
    res = _tools(openai=fake, news=news).news_sport("pl")
    assert res.ok and res.payload.get("source") == "rss"
    assert "Ze sportu" in res.text and "Wisła" in res.text


def test_sport_brief_unavailable_without_key_and_feeds() -> None:
    fake = _FakeOpenAi(available=False)
    res = _tools(openai=fake, news=_FakeNews({})).news_sport("pl")
    assert not res.ok and res.payload.get("reason") == "sport_unavailable"


def test_help_commands_walkthrough_is_spoken_and_bilingual() -> None:
    t = _tools(openai=_FakeOpenAi(available=False), news=_FakeNews())
    pl = t.help_commands("pl")
    en = t.help_commands("en")
    assert pl.ok and en.ok and pl.payload.get("kind") == "commands"
    # Covers the major groups, mentions the new briefs, and is TTS-safe.
    for probe in ("która godzina", "jakie wieści", "jak sport", "odtwórz notatki", "radio"):
        assert probe in pl.text
    assert "\n" not in pl.text and "*" not in pl.text
    assert "sports news" in en.text


def test_failed_web_search_is_explained_on_the_floor() -> None:
    """Verbose-state (2026-08-21): research attempted + whole cloud ladder dead →
    the RSS floor SAYS the web search failed instead of silently downgrading."""
    fake = _FakeResearchOpenAi(fail=True, text="")  # research fails, compose empty
    res = _tools(openai=fake, news=_FakeNews()).news_brief("pl")
    assert res.ok and res.text.startswith("Nie udało mi się przeszukać internetu")
    # Keyless (never attempted) floor keeps the plain reading — no false excuse.
    quiet = _tools(openai=_FakeOpenAi(available=False), news=_FakeNews()).news_brief("pl")
    assert quiet.ok and not quiet.text.startswith("Nie udało")
