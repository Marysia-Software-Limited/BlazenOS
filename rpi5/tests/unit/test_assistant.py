"""Tier 0 — the assistant prototype (offline, deterministic).

Covers name/wake detection, the PL+EN time parser, the persistent
notes/reminders store, and engine routing (remember / remind / recall /
news / chat) with a fake Gemini transport and an injected clock.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from blazend.domains.ai_orchestrator.adapters.rpi5.assistant import wake
from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.engine import Assistant, detect_lang
from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.gemini import GeminiClient, GeminiError
from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.news import NewsClient
from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.openai import OpenAiClient
from blazend.domains.context.adapters.rpi5.embeddings import EmbedderError
from blazend.domains.context.adapters.rpi5.memory import MemoryStore
from blazend.domains.context.adapters.rpi5.timeparse import parse_when
from blazend.domains.local_ai.adapters.rpi5.localllm import LocalLlm

NOW = datetime(2026, 6, 12, 14, 0, 0)
REPO = Path(__file__).resolve().parents[3]


# --- wake / name -----------------------------------------------------------
def test_wake_detection_pl_en():
    assert wake.is_wake("Hej Jessico, jaka godzina?")
    assert wake.is_wake("jessica what time is it")
    assert wake.is_wake("ok Jess")
    assert not wake.is_wake("jaka jest pogoda")


def test_strip_wake():
    assert wake.strip_wake("Hej Jessico, jaka godzina?") == "jaka godzina?"
    assert wake.strip_wake("Jessica what time is it") == "what time is it"
    assert wake.strip_wake("Jessica") == "Jessica"  # bare name, nothing to route


# --- time parsing ----------------------------------------------------------
def test_timeparse_relative():
    assert parse_when("za 10 minut", NOW) == NOW + timedelta(minutes=10)
    assert parse_when("in 2 hours", NOW) == NOW + timedelta(hours=2)
    assert parse_when("za godzinę", NOW) == NOW + timedelta(hours=1)
    assert parse_when("za 5 sekund", NOW) == NOW + timedelta(seconds=5)


def test_timeparse_clock_and_tomorrow():
    assert parse_when("o 15:30", NOW) == NOW.replace(hour=15, minute=30)
    # 09:00 is in the past relative to 14:00 → rolls to tomorrow.
    assert parse_when("o 9", NOW) == NOW.replace(hour=9, minute=0) + timedelta(days=1)
    assert parse_when("jutro o 8:00", NOW) == NOW.replace(hour=8, minute=0) + timedelta(days=1)
    assert parse_when("at 3pm", NOW) == NOW.replace(hour=15, minute=0)
    assert parse_when("kompletny bełkot", NOW) is None


# --- memory store ----------------------------------------------------------
def test_memory_notes_and_reminders(tmp_path):
    store = MemoryStore(tmp_path / "mem.json")
    store.add_note("kod do bramy to 4729", now=NOW)
    assert [n.text for n in store.recall("kod")] == ["kod do bramy to 4729"]
    assert store.recall("nieistnieje") == []

    store.add_reminder("zadzwoń do mamy", due=NOW + timedelta(seconds=5), now=NOW)
    assert store.due(NOW) == []  # not yet
    fired = store.due(NOW + timedelta(seconds=6))
    assert [r.text for r in fired] == ["zadzwoń do mamy"]
    assert store.due(NOW + timedelta(seconds=7)) == []  # fires once

    # persisted across reopen
    assert len(MemoryStore(tmp_path / "mem.json").notes()) == 1


def test_detect_lang():
    assert detect_lang("jaka jest godzina") == "pl"
    assert detect_lang("what is the time") == "en"


# --- engine routing --------------------------------------------------------
def _fake_transport(reply_text):
    def transport(_url, _body):
        return {"candidates": [{"content": {"parts": [{"text": reply_text}]}}]}
    return transport


def _assistant(tmp_path, *, gemini_reply=None, news_xml=""):
    gem = GeminiClient(
        api_key="test-key" if gemini_reply is not None else "",
        transport=_fake_transport(gemini_reply or ""),
    )
    # Inject an OFFLINE news client (empty feed by default) so news routing in
    # tests never touches the live network. Pass news_xml for an RSS fixture.
    news = NewsClient(transport=lambda _url: news_xml)
    return Assistant(memory=MemoryStore(tmp_path / "mem.json"), gemini=gem, news=news)


def test_engine_requires_wake(tmp_path):
    a = _assistant(tmp_path)
    assert a.route("jaka godzina", now=NOW).action == "asleep"
    assert a.route("Jessica", now=NOW).action == "wake"
    # now awake → follow-ups route without the name
    assert a.route("zapamiętaj że lubię kawę", now=NOW).action == "note"


def test_asleep_brushoff_speaks_only_for_command_like_utterances(tmp_path):
    # Short wake-less utterance while asleep — plausibly the user with the wake
    # word dropped by whisper — still gets the audible "Śpię…" brush-off…
    a = _assistant(tmp_path)
    short = a.route("włącz trójkę", now=NOW)
    assert short.action == "asleep" and "Śpię" in short.text

    # …but ambient prose (overheard TV after a false wake; live captures
    # 2026-07-13) is answered with SILENCE — empty text is never spoken.
    for tv in ("Dziesięć katastrof, dziesięć zasad. Żadna z nich nie narodziła "
               "się w Sztabie.",
               "przebicia, jak na przykład Amerykanie i Abramsy i na przykład "
               "komponenty to chyba.",
               "The previous owner sold it last year. Nobody knew why at the time."):
        amb = a.route(tv, now=NOW)
        assert amb.action == "asleep" and amb.text == "", tv
    assert not a.awake  # ambient speech never wakes her


def test_engine_remember_and_recall(tmp_path):
    a = _assistant(tmp_path)
    a.awake = True
    r = a.route("zapamiętaj, że hasło do wifi to lato2026", now=NOW)
    assert r.action == "note" and "lato2026" in r.text
    rec = a.route("co pamiętasz?", now=NOW)
    assert rec.action == "recall" and "lato2026" in rec.text


def test_engine_reminder_fires(tmp_path):
    a = _assistant(tmp_path)
    a.awake = True
    r = a.route("przypomnij mi żeby wyjąć pranie za 5 sekund", now=NOW)
    assert r.action == "reminder" and "pranie" in r.text
    assert a.due_reminders(NOW) == []
    fired = a.due_reminders(NOW + timedelta(seconds=6))
    assert len(fired) == 1 and "pranie" in fired[0].text


def test_engine_news_rss_fallback_without_key(tmp_path):
    # No Gemini key → the keyless RSS fallback still gives a brief.
    a = _assistant(tmp_path, news_xml=(
        "<rss><channel><title>F</title>"
        "<item><title>Nagłówek pierwszy</title></item></channel></rss>"
    ))
    a.awake = True
    r = a.route("co w wiadomościach?", now=NOW)
    assert r.action == "news" and r.data.get("source") == "rss"
    assert "Nagłówek pierwszy" in r.text


def test_engine_news_grounded_with_key(tmp_path):
    a = _assistant(tmp_path, gemini_reply="Dziś główna wiadomość to...")
    a.awake = True
    r = a.route("Jessica, sprawdź wiadomości", now=NOW)
    assert r.action == "news" and r.text.startswith("Dziś główna wiadomość")
    assert r.data.get("focus") == "krakow-poland"  # Kraków + Poland focused


def test_engine_news_falls_back_to_rss_when_gemini_errors(tmp_path):
    from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.news import NewsClient

    def boom(_url, _body):  # Gemini transport that fails (e.g. 429/billing)
        raise GeminiError("Gemini HTTP 429: credits depleted")
    gem = GeminiClient(api_key="test-key", transport=boom)
    rss = NewsClient(transport=lambda url: (
        "<rss><channel><title>F</title>"
        "<item><title>Wiadomość jeden</title></item>"
        "<item><title>Wiadomość dwa</title></item></channel></rss>"
    ))
    a = Assistant(memory=MemoryStore(tmp_path / "m.json"), gemini=gem, news=rss)
    a.awake = True
    r = a.route("co w wiadomościach", now=NOW)
    assert r.action == "news" and r.data.get("source") == "rss"
    assert "Wiadomość jeden" in r.text
    # Crucially: the raw Gemini error is NOT read aloud.
    assert "429" not in r.text and "http" not in r.text.lower()


def test_engine_news_short_error_when_all_unavailable(tmp_path):
    from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.news import NewsClient, NewsError

    class _DeadNews(NewsClient):
        def headlines(self, lang, limit=None):
            raise NewsError("all feeds down")
    a = Assistant(
        memory=MemoryStore(tmp_path / "m.json"),
        gemini=GeminiClient(api_key=""),  # no Gemini
        news=_DeadNews(transport=lambda url: ""),
    )
    a.awake = True
    r = a.route("co w wiadomościach", now=NOW)
    assert r.action == "error" and len(r.text) < 80  # short, spoken-friendly


def _radio_assistant(tmp_path, monkeypatch):
    monkeypatch.setenv("BLAZEN_CONFIG_ROOT", str(REPO / "configs"))
    a = Assistant(memory=MemoryStore(tmp_path / "m.json"), gemini=GeminiClient(api_key=""))
    a.awake = True
    return a


def test_engine_radio_play_trojka_pl(tmp_path, monkeypatch):
    a = _radio_assistant(tmp_path, monkeypatch)
    r = a.route("włącz Trójkę", now=NOW)
    assert r.action == "radio_play" and r.data["id"] == "trojka"
    assert r.data["url"].startswith("http") and "Trójk" in r.text


def test_engine_radio_play_radio_krakow_pl(tmp_path, monkeypatch):
    a = _radio_assistant(tmp_path, monkeypatch)
    r = a.route("puść Radio Kraków", now=NOW)
    assert r.action == "radio_play" and r.data["id"] == "radio-krakow"


def test_engine_radio_offer_when_unspecified_pl(tmp_path, monkeypatch):
    a = _radio_assistant(tmp_path, monkeypatch)
    r = a.route("włącz radio", now=NOW)
    assert r.action == "radio_offer" and "Trójka" in r.text and "?" in r.text


def test_engine_radio_stop_pl(tmp_path, monkeypatch):
    a = _radio_assistant(tmp_path, monkeypatch)
    r = a.route("wyłącz radio", now=NOW)
    assert r.action == "radio_stop"


def test_engine_radio_play_en(tmp_path, monkeypatch):
    a = _radio_assistant(tmp_path, monkeypatch)
    r = a.route("play the radio, channel three", now=NOW)
    assert r.action == "radio_play" and r.data["id"] == "trojka" and r.language == "en"


def test_engine_radio_does_not_reach_llm(tmp_path, monkeypatch):
    monkeypatch.setenv("BLAZEN_CONFIG_ROOT", str(REPO / "configs"))
    a = Assistant(
        memory=MemoryStore(tmp_path / "m.json"), gemini=GeminiClient(api_key=""),
        llm=LocalLlm(backend=_FakeLlm("SHOULD NOT BE CALLED")),
    )
    a.awake = True
    r = a.route("włącz Trójkę", now=NOW)
    assert r.action == "radio_play" and "SHOULD NOT" not in r.text


def _weather_client(*, temp=18.0, feels=17.0, code=3, wind=12.0, geo=None):
    from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.weather import WeatherClient

    def transport(url):
        if "geocoding-api" in url:
            return {"results": geo if geo is not None else []}
        return {"current": {
            "temperature_2m": temp, "apparent_temperature": feels,
            "weather_code": code, "wind_speed_10m": wind,
        }}
    return WeatherClient(transport=transport)


def test_engine_weather_defaults_to_krakow_pl(tmp_path):
    a = Assistant(
        memory=MemoryStore(tmp_path / "m.json"), gemini=GeminiClient(api_key=""),
        weather=_weather_client(temp=18.4, code=3),
    )
    a.awake = True
    r = a.route("jaka jest pogoda", now=NOW)  # no city → default Kraków
    assert r.action == "weather" and r.language == "pl"
    assert "Kraków" in r.text and "18" in r.text and "pochmurno" in r.text and "°C" in r.text


def test_engine_weather_named_city_geocoded_en(tmp_path):
    a = Assistant(
        memory=MemoryStore(tmp_path / "m.json"), gemini=GeminiClient(api_key=""),
        weather=_weather_client(temp=9.0, code=61,
                                geo=[{"name": "London", "latitude": 51.5, "longitude": -0.12}]),
    )
    a.awake = True
    r = a.route("what's the weather in London", now=NOW)
    assert r.action == "weather" and r.language == "en"
    assert "London" in r.text and "rain" in r.text.lower()


def test_engine_weather_does_not_reach_llm(tmp_path):
    # Weather must be answered by the weather client, never the chat model.
    a = Assistant(
        memory=MemoryStore(tmp_path / "m.json"), gemini=GeminiClient(api_key=""),
        llm=LocalLlm(backend=_FakeLlm("SHOULD NOT BE CALLED")),
        weather=_weather_client(temp=5.0, code=0),
    )
    a.awake = True
    r = a.route("pogoda", now=NOW)
    assert r.action == "weather" and "SHOULD NOT" not in r.text


def test_engine_chat_with_key(tmp_path):
    a = _assistant(tmp_path, gemini_reply="Mam się dobrze, dziękuję!")
    a.awake = True
    r = a.route("jak się masz?", now=NOW)
    assert r.action == "chat" and "dziękuję" in r.text


# --- local LLM (first) + OpenAI (second layer) -----------------------------
class _FakeLlm:
    """LlmBackend Protocol stand-in for the on-device model."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.last_system: str | None = None

    def generate(self, *, system: str, user: str) -> str:
        self.last_system = system
        return self.reply


def _openai(reply):
    def transport(_url, _headers, _body):
        return {"choices": [{"message": {"content": reply}}]}
    return OpenAiClient(api_key="test-key", transport=transport)


def test_engine_chat_prefers_local_llm(tmp_path):
    a = Assistant(
        memory=MemoryStore(tmp_path / "m.json"),
        gemini=GeminiClient(api_key=""),
        llm=LocalLlm(backend=_FakeLlm("Mam się świetnie.")),
        openai=_openai("z OpenAI"),
    )
    a.awake = True
    r = a.route("jak się masz?", now=NOW)
    assert r.action == "chat" and r.data.get("engine") == "local"
    assert "świetnie" in r.text


def test_engine_time_query_pl(tmp_path):
    a = Assistant(memory=MemoryStore(tmp_path / "m.json"), gemini=GeminiClient(api_key=""))
    a.awake = True
    r = a.route("która godzina", now=NOW)  # NOW = 14:00
    assert r.action == "time" and r.language == "pl"
    # Polish time-words, not digits: 14:00 → "czternasta". See assistant/plnum.py.
    assert "czternasta" in r.text.lower() and "godzina" in r.text.lower()


def test_engine_time_query_en(tmp_path):
    a = Assistant(memory=MemoryStore(tmp_path / "m.json"), gemini=GeminiClient(api_key=""))
    a.awake = True
    r = a.route("what time is it", now=NOW)
    assert r.action == "time" and r.language == "en"
    assert "2:00 PM" in r.text and "time" in r.text.lower()


def test_engine_date_query_pl(tmp_path):
    a = Assistant(memory=MemoryStore(tmp_path / "m.json"), gemini=GeminiClient(api_key=""))
    a.awake = True
    r = a.route("jaki dziś dzień", now=NOW)  # NOW = 2026-06-12
    assert r.action == "date" and r.language == "pl"
    assert "czerwca" in r.text and "2026" in r.text and "12" in r.text


def test_engine_date_query_en(tmp_path):
    a = Assistant(memory=MemoryStore(tmp_path / "m.json"), gemini=GeminiClient(api_key=""))
    a.awake = True
    r = a.route("what's the date", now=NOW)
    assert r.action == "date" and r.language == "en"
    assert "June" in r.text and "12" in r.text and "2026" in r.text


def test_engine_time_query_does_not_reach_llm(tmp_path):
    # The clock must be answered locally — never punted to the model.
    a = Assistant(
        memory=MemoryStore(tmp_path / "m.json"),
        gemini=GeminiClient(api_key=""),
        llm=LocalLlm(backend=_FakeLlm("SHOULD NOT BE CALLED")),
    )
    a.awake = True
    r = a.route("która jest godzina?", now=NOW)
    assert r.action == "time" and "SHOULD NOT" not in r.text


class _FakeStreamLlm(_FakeLlm):
    """Local backend that streams the reply token-by-token."""

    def __init__(self, chunks: list[str]) -> None:
        super().__init__("".join(chunks))
        self.chunks = chunks

    def generate_stream(self, *, system: str, user: str):
        self.last_system = system
        yield from self.chunks


def test_engine_streams_local_chat_sentence_by_sentence(tmp_path):
    backend = _FakeStreamLlm(["Mam się ", "świetnie", ". ", "A Ty", "?"])
    a = Assistant(
        memory=MemoryStore(tmp_path / "m.json"),
        gemini=GeminiClient(api_key=""),
        llm=LocalLlm(backend=backend),
    )
    a.awake = True
    spoken: list[tuple[str, str]] = []
    tokens = {"n": 0}
    r = a.route(
        "jak się masz?", now=NOW,
        on_sentence=lambda s, lang: spoken.append((lang, s)),
        on_token=lambda: tokens.__setitem__("n", tokens["n"] + 1),
    )
    # Each completed sentence dispatched the instant it finished, in order.
    assert spoken == [("pl", "Mam się świetnie."), ("pl", "A Ty?")]
    assert tokens["n"] == 1                       # on_token fires once (first token)
    assert r.action == "chat" and r.data == {"engine": "local", "streamed": True}
    assert r.text == "Mam się świetnie. A Ty?"   # full reply still returned


def test_engine_non_chat_path_ignores_streaming_hooks(tmp_path):
    # A command (remember) must not invoke the streaming sentence sink.
    a = Assistant(memory=MemoryStore(tmp_path / "m.json"), gemini=GeminiClient(api_key=""))
    a.awake = True
    spoken: list[str] = []
    r = a.route("zapamiętaj że kod to 4729", now=NOW,
                on_sentence=lambda s, lang: spoken.append(s))
    assert r.action == "note" and spoken == []
    assert not r.data.get("streamed")


def test_engine_chat_falls_back_to_openai(tmp_path):
    a = Assistant(
        memory=MemoryStore(tmp_path / "m.json"),
        gemini=GeminiClient(api_key=""),
        llm=None,
        openai=_openai("Cześć z OpenAI."),
    )
    a.awake = True
    r = a.route("opowiedz dowcip", now=NOW)
    assert r.action == "chat" and r.data.get("engine") == "openai"
    assert "OpenAI" in r.text


def test_engine_chat_canned_when_nothing(tmp_path):
    a = _assistant(tmp_path)  # no key, no llm, no openai
    a.awake = True
    r = a.route("jak się masz?", now=NOW)
    assert r.action == "chat" and r.data.get("needs_key")


def test_engine_news_never_uses_chat_llm(tmp_path):
    # News must NOT be answered by the local LLM / OpenAI chat layer — only
    # Gemini grounding or the RSS fallback.
    a = Assistant(
        memory=MemoryStore(tmp_path / "m.json"),
        gemini=GeminiClient(api_key=""),
        llm=LocalLlm(backend=_FakeLlm("local")),
        openai=_openai("openai"),
        news=NewsClient(transport=lambda _url: (
            "<rss><channel><title>F</title>"
            "<item><title>Świeży nagłówek</title></item></channel></rss>"
        )),
    )
    a.awake = True
    r = a.route("co w wiadomościach?", now=NOW)
    assert r.action == "news" and r.data.get("source") == "rss"
    assert r.data.get("engine") not in ("local", "openai")
    assert "local" not in r.text and "openai" not in r.text


# --- user's name (S2) ------------------------------------------------------
def test_engine_set_and_get_name_pl(tmp_path):
    a = _assistant(tmp_path)
    a.awake = True
    r = a.route("mam na imię Paweł", now=NOW)
    assert r.action == "profile" and r.data.get("name") == "Paweł" and "Paweł" in r.text
    g = a.route("jak mam na imię?", now=NOW)
    assert g.action == "profile" and "Paweł" in g.text


def test_engine_set_name_en_persists_across_instances(tmp_path):
    path = tmp_path / "mem.json"
    a = Assistant(memory=MemoryStore(path), gemini=GeminiClient(api_key=""))
    a.awake = True
    a.route("my name is Alex", now=NOW)
    a2 = Assistant(memory=MemoryStore(path), gemini=GeminiClient(api_key=""))
    a2.awake = True
    g = a2.route("what's my name?", now=NOW)
    assert g.action == "profile" and "Alex" in g.text


def test_engine_get_name_unknown(tmp_path):
    a = _assistant(tmp_path)
    a.awake = True
    r = a.route("jak mam na imię?", now=NOW)
    assert r.action == "profile" and r.data.get("name") is None


def test_engine_greets_by_name_on_wake(tmp_path):
    a = _assistant(tmp_path)
    a.awake = True
    a.route("mów do mnie Ola", now=NOW)
    r = a.route("Jessica", now=NOW)
    assert r.action == "wake" and "Ola" in r.text


def test_engine_chat_injects_name_into_system_prompt(tmp_path):
    fake = _FakeLlm("ok")
    a = Assistant(
        memory=MemoryStore(tmp_path / "m.json"),
        gemini=GeminiClient(api_key=""),
        llm=LocalLlm(backend=fake),
    )
    a.awake = True
    a.route("mam na imię Zofia", now=NOW)
    a.route("opowiedz coś o kawie", now=NOW)
    assert "Zofia" in (fake.last_system or "")


# --- titled notes + semantic recall (RAG) ----------------------------------
class _FakeEmbedder:
    """Deterministic 3-axis embedder (weekend / wifi / generic) for tests."""

    name = "fake-emb"
    available = True

    def embed(self, texts, *, kind="passage"):
        return [self._vec(t) for t in texts]

    @staticmethod
    def _vec(t):
        t = t.lower()
        weekend = 1.0 if ("weekend" in t or "gór" in t) else 0.0
        wifi = 1.0 if ("wifi" in t or "hasło" in t) else 0.0
        return [weekend, wifi, 0.1]


def test_remember_titled_note_splits_title_and_content(tmp_path):
    a = _assistant(tmp_path)
    a.awake = True
    r = a.route("zapamiętaj: plan na weekend. Chcę pojechać w góry i odpocząć", now=NOW)
    assert r.action == "note"
    assert r.data["title"] == "plan na weekend"
    assert "góry" in r.data["text"] and "weekend" not in r.data["text"]
    assert "plan na weekend" in r.text  # speaks the title back, not the long body


def test_remember_untitled_note_back_compat(tmp_path):
    a = _assistant(tmp_path)
    a.awake = True
    r = a.route("zapamiętaj że hasło do wifi to lato2026", now=NOW)
    assert r.action == "note" and r.data["title"] == "" and "lato2026" in r.data["text"]


def test_chat_injects_relevant_note_into_system_prompt(tmp_path):
    fake = _FakeLlm("ok")
    a = Assistant(
        memory=MemoryStore(tmp_path / "m.json"),
        gemini=GeminiClient(api_key=""),
        llm=LocalLlm(backend=fake),
        embedder=_FakeEmbedder(),
    )
    a.awake = True
    a.route("zapamiętaj: plan na weekend. Chcę pojechać w góry", now=NOW)
    a.route("zapamiętaj że hasło do wifi to lato2026", now=NOW)
    a.route("co planuję w weekend?", now=NOW)
    system = fake.last_system or ""
    assert "góry" in system        # the relevant note was retrieved + injected
    assert "lato2026" not in system  # the irrelevant note was filtered by score


def test_remember_title_with_question_mark_separator(tmp_path):
    a = _assistant(tmp_path)
    a.awake = True
    r = a.route("zapamiętaj: czy kupić mleko? tak, dwa litry", now=NOW)
    assert r.action == "note"
    assert r.data["title"] == "czy kupić mleko" and "dwa litry" in r.data["text"]


def test_chat_no_note_context_when_embedder_absent(tmp_path):
    # No embedder → lexical fallback, nothing injected into the system prompt.
    fake = _FakeLlm("ok")
    a = Assistant(
        memory=MemoryStore(tmp_path / "m.json"),
        gemini=GeminiClient(api_key=""),
        llm=LocalLlm(backend=fake),
    )
    a.awake = True
    a.route("zapamiętaj: plan na weekend. góry", now=NOW)
    a.route("co planuję?", now=NOW)
    system = (fake.last_system or "").lower()
    assert "notatki użytkownika" not in system and "saved notes" not in system


def test_remember_survives_embedder_failure(tmp_path):
    class _BoomEmbedder:
        name = "boom"
        available = True

        def embed(self, texts, *, kind="passage"):
            raise EmbedderError("onnx blew up")

    a = Assistant(
        memory=MemoryStore(tmp_path / "m.json"),
        gemini=GeminiClient(api_key=""),
        embedder=_BoomEmbedder(),
    )
    a.awake = True
    r = a.route("zapamiętaj że kod do bramy to 4729", now=NOW)
    assert r.action == "note" and "4729" in r.data["text"]  # stored despite embed error


def test_assistant_backfills_existing_notes_on_init(tmp_path):
    p = tmp_path / "m.json"
    mem = MemoryStore(p)
    mem.add_note("góry weekend", now=NOW)  # pre-existing, no embedding yet
    assert mem.notes_missing_embeddings(model="fake-emb") == mem.notes()
    # Constructing an Assistant with an embedder backfills the missing vectors.
    Assistant(memory=MemoryStore(p), gemini=GeminiClient(api_key=""), embedder=_FakeEmbedder())
    assert MemoryStore(p).notes_missing_embeddings(model="fake-emb") == []


def test_chat_context_respects_top_k(tmp_path):
    fake = _FakeLlm("ok")
    a = Assistant(
        memory=MemoryStore(tmp_path / "m.json"),
        gemini=GeminiClient(api_key=""),
        llm=LocalLlm(backend=fake),
        embedder=_FakeEmbedder(),
        notes_top_k=2,
    )
    a.awake = True
    for i in range(6):  # six equally-relevant weekend notes
        a.route(f"zapamiętaj że weekend numer {i}", now=NOW)
    a.route("co o weekend?", now=NOW)
    system = fake.last_system or ""
    assert "numer" in system and system.count("numer") <= 2  # capped at top_k


def test_notes_context_respects_char_budget(tmp_path):
    a = Assistant(
        memory=MemoryStore(tmp_path / "m.json"),
        gemini=GeminiClient(api_key=""),
        embedder=_FakeEmbedder(),
        notes_top_k=10,
        notes_max_chars=20,
    )
    a.awake = True
    a.route("zapamiętaj że weekend aaa", now=NOW)  # short — fits the budget
    a.route("zapamiętaj że weekend " + "b" * 30, now=NOW)  # long — overflows it
    ctx = a._notes_context("co o weekend?", "pl")
    assert "aaa" in ctx and "bbbb" not in ctx  # long note dropped by the char budget


# --- reminders / alarms / events (S4) --------------------------------------
def test_engine_reminder_task_text_is_clean(tmp_path):
    a = _assistant(tmp_path)
    a.awake = True
    r = a.route("Hej Jessico, przypomnij mi za godzinę, że muszę otworzyć pokój", now=NOW)
    assert r.action == "reminder"
    assert r.data["text"] == "muszę otworzyć pokój"  # no stray "Hej"/commas
    assert "," not in r.text.split(":")[1]  # task half has no orphan commas


def test_engine_alarm_vocabulary(tmp_path):
    a = _assistant(tmp_path)
    a.awake = True
    r = a.route("ustaw alarm za 10 minut", now=NOW)
    assert r.action == "reminder" and r.data["category"] == "alarm"
    assert "udzik" in r.text or "larm" in r.text  # "budzik"/"alarm" wording


def test_engine_event_vocabulary(tmp_path):
    a = _assistant(tmp_path)
    a.awake = True
    r = a.route("dodaj wydarzenie spotkanie z Anną o 15:00", now=NOW)
    assert r.action == "reminder" and r.data["category"] == "event"
    assert "spotkanie z Anną" in r.data["text"]


def test_engine_alarm_vocabulary_en(tmp_path):
    a = _assistant(tmp_path)
    a.awake = True
    r = a.route("set an alarm in 10 minutes", now=NOW)
    assert r.action == "reminder" and r.data["category"] == "alarm"


def test_engine_recall_reminders_broadened(tmp_path):
    a = _assistant(tmp_path)
    a.awake = True
    a.route("ustaw alarm za 10 minut", now=NOW)
    r = a.route("co mam zaplanowane", now=NOW)
    assert r.action == "recall" and "budzik" in r.text  # the broadened phrase lists it


def test_engine_due_reminder_spoken_without_emoji(tmp_path):
    a = _assistant(tmp_path)
    a.awake = True
    a.route("przypomnij mi za 5 sekund, że pranie", now=NOW)
    fired = a.due_reminders(NOW + timedelta(seconds=6))
    assert len(fired) == 1
    assert "⏰" not in fired[0].text and "pranie" in fired[0].text
    assert fired[0].text.startswith("Przypominam")


# --- voice notes (S3) ------------------------------------------------------
def test_memory_voice_notes_persist(tmp_path):
    p = tmp_path / "m.json"
    MemoryStore(p).add_voice_note(tmp_path / "x.wav", now=NOW, duration_s=3.5, transcript="hej")
    vns = MemoryStore(p).voice_notes()
    assert len(vns) == 1 and vns[0].duration_s == 3.5 and vns[0].audio_path.endswith("x.wav")


def test_engine_voice_note_record_intent(tmp_path):
    a = _assistant(tmp_path)
    a.awake = True
    assert a.route("nagraj notatkę głosową", now=NOW).action == "voice_note_record"
    assert a.route("record a voice note", now=NOW).action == "voice_note_record"


def test_engine_voice_note_play_empty(tmp_path):
    a = _assistant(tmp_path)
    a.awake = True
    r = a.route("odtwórz notatki", now=NOW)
    assert r.action == "voice_note_play" and r.data["paths"] == []


def test_engine_voice_note_play_lists_stored(tmp_path):
    mem = MemoryStore(tmp_path / "m.json")
    mem.add_voice_note(tmp_path / "a.wav", now=NOW, duration_s=2.0)
    a = Assistant(memory=mem, gemini=GeminiClient(api_key=""), always_awake=True)
    r = a.route("play my voice notes", now=NOW)
    assert r.action == "voice_note_play" and len(r.data["paths"]) == 1


def test_engages_probe_is_pure_and_matches_route_gating(tmp_path):
    """`engages()` predicts whether route() will reach a real handler (the slow
    path worth a "Chwileczkę." cue) WITHOUT mutating awake state — so the brain
    can cue before routing, and never cues an asleep brush-off or a bare wake."""
    a = _assistant(tmp_path)
    # Asleep: a plain command must not engage (route would answer "Śpię…").
    assert not a.engages("jaka godzina")
    assert not a.awake  # pure probe — no state change
    # A bare wake engages nothing (route answers "Tak? Słucham." instantly).
    assert not a.engages("Jessica")
    assert not a.engages("")
    # Wake + command engages even from asleep.
    assert a.engages("Jessica, opowiedz mi o Krakowie")
    assert not a.awake  # still pure
    # Awake: any command engages.
    a.awake = True
    assert a.engages("opowiedz mi o Krakowie")
    assert not a.engages("")  # empty transcript never engages


# --- memory privacy: cloud backends don't see notes (share_with_cloud) -----
class _CapBackend:
    """Fake router backend that records the system prompt it was given."""

    def __init__(self) -> None:
        self.seen: str | None = None
        self.available = True

    def chat(self, text: str, system: str = "") -> str:
        self.seen = system
        return "ok"


class _OneBackendRouter:
    def __init__(self, name: str, backend: _CapBackend) -> None:
        self._route = [(name, backend)]

    def route(self, task):  # noqa: ANN001, ANN201 — mirrors ModelRouter.route
        return list(self._route)


def _with_memories(a: Assistant) -> Assistant:
    a._notes_context = lambda text, lang: " PAMIĘĆ: sekretna notatka."  # type: ignore[method-assign]
    return a


def test_local_backend_receives_the_memory_block(tmp_path):
    backend = _CapBackend()
    a = _with_memories(Assistant(
        memory=MemoryStore(tmp_path / "m.json"), gemini=GeminiClient(api_key=""),
        router=_OneBackendRouter("bielik-1.5b", backend)))
    a._chat("jak działa filtr?", "pl")
    assert backend.seen is not None and "sekretna notatka" in backend.seen


def test_cloud_backend_is_denied_the_memory_block_by_default(tmp_path):
    backend = _CapBackend()
    a = _with_memories(Assistant(
        memory=MemoryStore(tmp_path / "m.json"), gemini=GeminiClient(api_key=""),
        router=_OneBackendRouter("gpt-5.5", backend)))
    a._chat("jak działa filtr?", "pl")
    assert backend.seen is not None and "sekretna notatka" not in backend.seen
    assert "Dżesika" in backend.seen or "Jessica" in backend.seen  # persona intact


def test_share_with_cloud_knob_opts_in(tmp_path):
    backend = _CapBackend()
    a = _with_memories(Assistant(
        memory=MemoryStore(tmp_path / "m.json"), gemini=GeminiClient(api_key=""),
        router=_OneBackendRouter("gpt-5.5", backend),
        notes_share_with_cloud=True))
    a._chat("jak działa filtr?", "pl")
    assert backend.seen is not None and "sekretna notatka" in backend.seen


def test_gemini_chat_fallback_is_denied_memories_by_default(tmp_path):
    import json as _json
    seen: dict = {}

    def transport(url, body):  # noqa: ANN001, ANN202
        seen["body"] = body
        return {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}

    gem = GeminiClient(api_key="test-key", transport=transport)
    a = _with_memories(Assistant(
        memory=MemoryStore(tmp_path / "m.json"), gemini=gem,
        news=NewsClient(transport=lambda _url: "")))
    a._chat("opowiedz coś ciekawego", "pl")
    assert seen and "sekretna notatka" not in _json.dumps(seen["body"], ensure_ascii=False)


def test_openai_retries_without_temperature_for_reasoning_models():
    """gpt-5.6-sol-style models 400 on any non-default `temperature`; the client
    must drop the param and retry once instead of failing the cloud path."""
    from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.openai import OpenAiError

    calls: list[dict] = []

    def transport(_url, _headers, body):  # noqa: ANN001, ANN202
        calls.append(body)
        if "temperature" in body:
            raise OpenAiError(
                "OpenAI HTTP 400: Unsupported value: 'temperature' does not "
                "support 0.4 with this model."
            )
        return {"choices": [{"message": {"content": "ok"}}]}

    c = OpenAiClient(api_key="test-key", model="gpt-5.6-sol", transport=transport)
    assert c.chat("ping") == "ok"
    assert len(calls) == 2 and "temperature" not in calls[1]


def test_openai_does_not_retry_other_errors():
    from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.openai import OpenAiError

    def transport(_url, _headers, _body):  # noqa: ANN001, ANN202
        raise OpenAiError("OpenAI HTTP 401: bad key")

    c = OpenAiClient(api_key="test-key", model="gpt-5.6-sol", transport=transport)
    try:
        c.chat("ping")
        raise AssertionError("expected OpenAiError")
    except OpenAiError as e:
        assert "401" in str(e)
