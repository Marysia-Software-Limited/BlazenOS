"""The assistant engine — Jessica's conversation logic.

Ties the pieces together: name/wake gating, the remember / remind / recall
commands (bilingual), news + site lookups via Gemini, and freeform Polish
conversation via Gemini. Pure and synchronous so it is trivially testable;
the REPL and the IPC `blazend-brain` unit both drive it.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from blazend.domains.ai_orchestrator.adapters.rpi5.assistant import wake
from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.gemini import GeminiClient, GeminiError
from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.news import NewsClient, NewsError
from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.openai import OpenAiClient, OpenAiError
from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.radio import RadioDirectory
from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.sentences import SentenceSlicer
from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.weather import (
    Place,
    WeatherClient,
    WeatherError,
    describe_code,
)
from blazend.domains.ai_orchestrator.core.model_router import ModelRouter, Task
from blazend.domains.context.adapters.rpi5.embeddings import EmbedderError, EmbedderLike
from blazend.domains.context.adapters.rpi5.memory import MemoryStore, Note
from blazend.domains.context.adapters.rpi5.timeparse import parse_when
from blazend.domains.local_ai.adapters.rpi5.localllm import Llm, LlmError

# Streaming hooks for sentence-sliced TTS. ``on_sentence(text, language)`` fires
# the instant a sentence completes; ``on_token()`` fires on the first token (for
# latency instrumentation). Both optional — absent → the old whole-reply path.
OnSentence = Callable[[str, str], None]
OnToken = Callable[[], None]

log = logging.getLogger("blazend.domains.ai_orchestrator.adapters.rpi5.assistant")

PERSONA = (
    "Jesteś Jessica — głosowa asystentka osobista dla osób niewidomych i "
    "słabowidzących, działająca na Raspberry Pi 5. Odpowiadaj w języku "
    "użytkownika (polski lub angielski); domyślnie po polsku. Mów krótko — "
    "jedno lub dwa zdania, chyba że poproszono o szczegóły. Bądź konkretna i "
    "uczciwa; jeśli czegoś nie wiesz, powiedz to wprost."
)

_PL_HINT = re.compile(r"[ąćęłńóśźż]|\b(jest|czy|co|jak|mi|że|nie|tak|godzina|przypomnij|zapamiętaj|wiadomości)\b", re.IGNORECASE)
_EN_HINT = re.compile(r"\b(the|what|is|remind|remember|news|please|time|tomorrow|at)\b", re.IGNORECASE)


def detect_lang(text: str) -> str:
    """Heuristic language detector. Polish-first per the persona."""
    if _PL_HINT.search(text):
        return "pl"
    if _EN_HINT.search(text):
        return "en"
    return "pl"


def _t(lang: str, pl: str, en: str) -> str:
    return pl if lang == "pl" else en


# --- command patterns (PL + EN) ------------------------------------------
_RECALL = re.compile(
    r"\b(co\s+(pamiętasz|zapamiętała|zapisała)|jakie\s+mam\s+notatki|moje\s+notatki|"
    r"what\s+do\s+you\s+remember|list\s+notes|my\s+notes)\b",
    re.IGNORECASE,
)
_LIST_REMINDERS = re.compile(
    r"\b(jakie\s+mam\s+(przypomnienia|alarmy|wydarzenia)|"
    r"moje\s+(przypomnienia|alarmy|wydarzenia)|"
    r"co\s+mam\s+(zaplanowane|w\s+planach|na\s+dziś|na\s+dzis)|"
    r"(o\s+czym|kiedy)\s+masz\s+mi\s+przypomnieć|"
    r"list\s+(reminders|alarms)|what\s+(reminders|alarms)|"
    r"my\s+(reminders|alarms)|what\s+do\s+i\s+have(\s+planned)?)\b",
    re.IGNORECASE,
)
_REMIND = re.compile(
    r"\b(przypomnij(\s+mi)?|remind\s+me|"
    r"(ustaw|nastaw)\s+(alarm|budzik)|"
    r"set\s+(an?\s+)?(alarm|timer|reminder)|wake\s+me(\s+up)?|"
    r"dodaj\s+(wydarzenie|spotkanie)|add\s+(an?\s+)?(event|meeting))\b",
    re.IGNORECASE,
)
# Categorise a remind-ish utterance for the reply wording + stored category.
_ALARM_HINT = re.compile(r"\b(alarm|budzik|wake\s+me|timer)\b", re.IGNORECASE)
_EVENT_HINT = re.compile(r"\b(wydarzenie|spotkanie|event|meeting)\b", re.IGNORECASE)
_REMEMBER = re.compile(r"\b(zapamiętaj|zapamietaj|zapisz|remember)\b", re.IGNORECASE)
# Clock / calendar — a screenless assistant on a device with a clock must
# answer these locally, never punt to the LLM ("I can't access the time").
_TIME_QUERY = re.compile(
    r"\b(która\s+(jest\s+)?godzina|jaka\s+(jest\s+)?godzina|"
    r"what\s+time\s+is\s+it|what'?s\s+the\s+time)\b",
    re.IGNORECASE,
)
_DATE_QUERY = re.compile(
    r"\b(jaki\s+(jest\s+)?(dziś|dzis|dzisiaj)\s+dzień|"
    r"który\s+(jest\s+)?(dziś|dzis|dzisiaj)\s+dzień|"
    r"jaka\s+(jest\s+)?(dziś|dzis|dzisiaj\s+)?data|którego\s+(dziś|dzis|dzisiaj)|"
    r"what'?s?\s+(the\s+)?(today'?s\s+)?date|what\s+day\s+is\s+(it|today)|what'?s\s+today)\b",
    re.IGNORECASE,
)
# Locale-independent day/month names (don't depend on the system locale).
_PL_DAYS = ("poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota", "niedziela")
_PL_MONTHS = (
    "stycznia", "lutego", "marca", "kwietnia", "maja", "czerwca",
    "lipca", "sierpnia", "września", "października", "listopada", "grudnia",
)
_EN_DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_EN_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
# User's name: ask ("jak mam na imię" / "what's my name") vs set ("mam na imię X").
_NAME_GET = re.compile(
    r"\b(jak\s+(mam\s+na\s+imię|mam\s+na\s+imie|się\s+nazywam|sie\s+nazywam)|"
    r"what'?s\s+my\s+name|what\s+is\s+my\s+name|who\s+am\s+i)\b",
    re.IGNORECASE,
)
_NAME_SET = re.compile(
    r"\b(?:nazywam\s+się|nazywam\s+sie|mam\s+na\s+imię|mam\s+na\s+imie|"
    r"mów\s+do\s+mnie|mow\s+do\s+mnie|my\s+name\s+is|call\s+me)\s+"
    r"([A-Za-zÀ-ÖØ-öø-ž'’\-]+)",
    re.IGNORECASE,
)
# Voice notes: record an audio memo vs play stored memos back.
_VOICE_NOTE_REC = re.compile(
    r"\b(nagraj|zostaw)\s+(mi\s+)?(notatkę|notatke)(\s+głosow\w*)?\b|"
    r"\brecord\s+(a\s+)?(voice\s+)?(note|memo)\b|\bleave\s+(a\s+)?(voice\s+)?(note|memo)\b",
    re.IGNORECASE,
)
_VOICE_NOTE_PLAY = re.compile(
    r"\b(odtwórz|odtworz|odsłuchaj|odsluchaj|puść|pusc)\s+(moje\s+|mi\s+)?"
    r"(notatki|notatkę|notatke|nagrania)\b|"
    r"\bplay\s+(my\s+)?(voice\s+)?(notes?|memos?|recordings?)\b",
    re.IGNORECASE,
)
_NEWS = re.compile(
    r"\b(wiadomo\w*|co\s+(nowego|słychać|slychac)|aktualno\w*|"
    r"news|headlines|what'?s\s+happening)\b",
    re.IGNORECASE,
)
_WEATHER = re.compile(
    r"\b(pogod\w*|jaka\s+(jest\s+)?aura|czy\s+(będzie|bedzie)\s+pad\w*|"
    r"weather|forecast|temperatur\w*|is\s+it\s+(going\s+to\s+)?(rain|snow))\b",
    re.IGNORECASE,
)
# "...w Krakowie" / "...in Kraków" — pull the city out of a weather question.
_WEATHER_CITY = re.compile(
    r"\b(?:w(?:e)?|in)\s+([A-Za-zÀ-ÖØ-öø-ż'’\- ]+?)\s*$",
    re.IGNORECASE,
)
# Radio: stop first (more specific), then a play/offer trigger. The play check
# fires on the word "radio", a known station word, or a resolved station name.
_RADIO_STOP = re.compile(
    r"\b(wyłącz|wylacz|zatrzymaj|zgaś|zgas|przestań|przestan)\b.*\b(radio|muzyk\w*|stacj\w*|gra\w*)\b|"
    r"\b(stop|turn\s+off|pause)\b.*\b(radio|music|station|playing)\b",
    re.IGNORECASE,
)
_RADIO_KW = re.compile(
    r"\bradio\b|\btrójk\w*|\btrojk\w*|\bjedynk\w*|\bdwójk\w*|\bdwojk\w*|"
    r"\bczwórk\w*|\bczwork\w*|\brmf\b|\bnowy\s+świat\b|\bnowy\s+swiat\b",
    re.IGNORECASE,
)
_SITE = re.compile(
    r"\b(stron[aęy]|otwórz|otworz|sprawdź\s+na|sprawdz\s+na|site|open|summari[sz]e|streść|stresc)\b|https?://",
    re.IGNORECASE,
)
# Open knowledge questions (advanced science, "why/how does…", explanations,
# web research) → route to OPEN_QA (gpt-5.5 first). Simple chat/commands stay on
# the fast local model. Misrouting is harmless: with no OpenAI key OPEN_QA just
# falls to the 11B/local Bielik anyway.
_OPEN_QA = re.compile(
    r"\b(dlaczego|czemu|wyja(ś|s)nij|wyt(ł|l)umacz|co\s+to\s+(jest|za)|czym\s+(jest|s(ą|a))|"
    r"jak\s+(dzia(ł|l)a|dzia(ł|l)aj(ą|a)|powsta(ł|l)|to\s+mo(ż|z)liwe)|opowiedz\s+o|"
    r"why|how\s+(does|do|did|to|is|are|can)|what\s+(is|are|causes|caused|happens)|"
    r"explain|tell\s+me\s+about|who\s+(is|was|were))\b",
    re.IGNORECASE,
)
# Strip the leading "to/że/mi/that" filler after a command verb.
_REMEMBER_TAIL = re.compile(r"^(że|ze|to|that|:|,)\s+", re.IGNORECASE)
# A time expression to split off a reminder's task text.
_TIME_SPAN = re.compile(
    r"\b((za|in)\s+(\d+\s+)?\w+|((o|at)\s+\d{1,2}(:\d{2})?\s*(am|pm)?))\b"
    r"|\b(jutro|tomorrow)\b",
    re.IGNORECASE,
)
# Leading filler left over once the verb + time are removed from a command
# ("mi", "żeby", "że", a greeting, stray commas) — stripped iteratively.
_LEAD_FILLER = re.compile(
    r"^(?:mi|mnie|me|że|ze|żeby|zeby|aby|abym|o\s+tym,?\s*że|to|that|about|"
    r"hej|hey|cześć|czesc|ok|okej|okay)\b[\s,]*",
    re.IGNORECASE,
)


def _tidy(text: str) -> str:
    """Collapse whitespace and trim stray edge punctuation/commas."""
    text = re.sub(r"\s+", " ", text)
    return text.strip(" ,.:;!?–—-")


def _strip_lead_filler(text: str) -> str:
    """Iteratively drop leading filler words/punctuation from a task phrase."""
    text = _tidy(text)
    prev = None
    while prev != text:
        prev = text
        text = _tidy(_LEAD_FILLER.sub("", text))
    return text


def _split_title_content(body: str) -> tuple[str, str]:
    """Split a remembered note into a short title + the rest of the content.

    The title is everything up to the first sentence break ("zapamiętaj:
    <title>. <content>"). ASR frequently drops punctuation, so a body with no
    break — or with an empty side — stays a single untitled blob (title empty),
    which is the original, back-compatible behaviour.
    """
    m = re.search(r"[.?!\n]", body)
    if not m:
        return "", body.strip()
    title = body[: m.start()].strip()
    content = body[m.end() :].strip()
    if not title or not content:
        return "", body.strip()
    return title, content


@dataclass
class Reply:
    """The assistant's response to one utterance."""

    text: str
    language: str = "pl"
    # wake | asleep | note | reminder | recall | news | chat | profile | error
    #   | voice_note_record | voice_note_play | time | date | weather
    #   | radio_play | radio_stop | radio_offer
    action: str = "chat"
    data: dict[str, Any] = field(default_factory=dict)


class Assistant:
    """Stateful conversation engine. ``route()`` handles one utterance."""

    def __init__(
        self,
        *,
        memory: MemoryStore | None = None,
        gemini: GeminiClient | None = None,
        llm: Llm | None = None,
        router: ModelRouter | None = None,
        openai: OpenAiClient | None = None,
        embedder: EmbedderLike | None = None,
        weather: WeatherClient | None = None,
        radio: RadioDirectory | None = None,
        news: NewsClient | None = None,
        persona: str = PERSONA,
        always_awake: bool = False,
        notes_top_k: int = 4,
        notes_min_score: float = 0.82,
        notes_rel_margin: float = 0.06,
        notes_max_chars: int = 1200,
    ):
        self.memory = memory or MemoryStore()
        self.gemini = gemini or GeminiClient()
        # On-device LLM for freeform chat. Defaults to None so unit tests stay
        # offline/deterministic; the runner injects a real LocalLlm().
        self.llm = llm
        # Task-based backend router (COMMAND→1.5B, RECOMMEND→4.5B, OPEN_QA→gpt-5.5,
        # all→11B-Ollama when up). When set it supersedes the legacy `llm`/`openai`
        # chain in `_chat`; when None (unit tests) the legacy chain is used.
        self.router = router
        # Weather via Open-Meteo (keyless, on-device HTTP). Lazily built so the
        # default (Kraków) is always available; tests inject a fake transport.
        self.weather = weather or WeatherClient()
        # Internet-radio catalogue (configs/radio.yaml). The runner does the
        # actual streaming; the engine only resolves the spoken station name.
        self.radio = radio if radio is not None else RadioDirectory()
        # Keyless RSS news fallback for when Gemini grounding is unavailable.
        self.news = news if news is not None else NewsClient()
        # Cloud second layer behind the local LLM (OpenAI). Local stays first.
        self.openai = openai
        # On-device embedder for semantic note recall. None → lexical fallback.
        self._embedder = embedder
        self._embedder_model = str(getattr(embedder, "name", "")) if embedder else ""
        self._notes_top_k = notes_top_k
        self._notes_min_score = notes_min_score
        self._notes_rel_margin = notes_rel_margin
        self._notes_max_chars = notes_max_chars
        self.persona = persona
        self.awake = always_awake
        self._always_awake = always_awake
        if embedder is not None and embedder.available:
            self._backfill_embeddings()

    # -- semantic note embeddings --------------------------------------
    def _backfill_embeddings(self) -> None:
        """Embed any pre-existing notes that lack a stored vector (one-time)."""
        for note in self.memory.notes_missing_embeddings(model=self._embedder_model):
            self._embed_note(note)

    def _embed_note(self, note: Note) -> None:
        if not (self._embedder and self._embedder.available):
            return
        blob = f"{note.title}. {note.text}" if note.title else note.text
        try:
            vector = self._embedder.embed([blob], kind="passage")[0]
        except EmbedderError as e:
            log.warning("note embedding failed (%s); semantic recall degraded", e)
            return
        self.memory.set_note_embedding(note.id, vector, model=self._embedder_model)

    def _notes_context(self, text: str, lang: str) -> str:
        """Retrieve the user's notes relevant to ``text`` for the LLM prompt."""
        if not (self._embedder and self._embedder.available):
            return ""
        try:
            qvec = self._embedder.embed([text], kind="query")[0]
        except EmbedderError:
            return ""
        hits = self.memory.search_notes_semantic(
            qvec,
            limit=self._notes_top_k,
            min_score=self._notes_min_score,
            rel_margin=self._notes_rel_margin,
        )
        if not hits:
            return ""
        header = _t(
            lang,
            " Zapisane notatki użytkownika (wykorzystaj, jeśli pomocne):",
            " The user's saved notes (use them if relevant):",
        )
        budget = self._notes_max_chars
        parts: list[str] = []
        for n in hits:
            piece = f" [{n.title}] {n.text}" if n.title else f" {n.text}"
            if len(piece) > budget:
                break
            parts.append(piece)
            budget -= len(piece)
        return header + "".join(parts) if parts else ""

    # -- top-level -----------------------------------------------------
    def route(
        self,
        text: str,
        *,
        now: datetime,
        on_sentence: OnSentence | None = None,
        on_token: OnToken | None = None,
    ) -> Reply:
        text = text.strip()
        lang = detect_lang(text)
        if not text:
            return Reply("", lang, "asleep")

        named = wake.is_wake(text)
        if named:
            self.awake = True
            command = wake.strip_wake(text)
            if not command or command == text and wake.is_wake(command):
                command = ""
        elif self.awake:
            command = text
        else:
            return Reply(
                _t(lang, "Śpię — powiedz „Jessica”, żeby mnie obudzić.",
                   "I'm asleep — say \"Jessica\" to wake me."),
                lang, "asleep",
            )

        if not command:
            name = self.memory.get_profile("name")
            if name:
                return Reply(_t(lang, f"Tak, {name}? Słucham.", f"Yes, {name}? I'm listening."), lang, "wake")
            return Reply(_t(lang, "Tak? Słucham.", "Yes? I'm listening."), lang, "wake")

        return self._handle(command, lang, now=now, on_sentence=on_sentence, on_token=on_token)

    # -- command handling ----------------------------------------------
    def _handle(
        self,
        text: str,
        lang: str,
        *,
        now: datetime,
        on_sentence: OnSentence | None = None,
        on_token: OnToken | None = None,
    ) -> Reply:
        if _NAME_GET.search(text):
            return self._get_name(lang)
        if _NAME_SET.search(text):
            return self._set_name(text, lang, now=now)
        if _DATE_QUERY.search(text):
            return self._say_date(lang, now)
        if _TIME_QUERY.search(text):
            return self._say_time(lang, now)
        if _VOICE_NOTE_PLAY.search(text):
            return self._play_voice_notes(lang)
        if _VOICE_NOTE_REC.search(text):
            return self._record_voice_note(lang)
        if _LIST_REMINDERS.search(text):
            return self._recall_reminders(lang)
        if _RECALL.search(text):
            return self._recall_notes(text, lang)
        if _REMIND.search(text):
            return self._remind(text, lang, now=now)
        if _REMEMBER.search(text):
            return self._remember(text, lang, now=now)
        if _WEATHER.search(text):
            return self._weather(text, lang)
        if _NEWS.search(text):
            return self._news(lang)
        if _SITE.search(text):
            return self._lookup(text, lang)
        if _RADIO_STOP.search(text):
            return self._radio_stop(lang)
        if _RADIO_KW.search(text) or self.radio.resolve(text) is not None:
            return self._radio(text, lang)
        return self._chat(text, lang, task=self._task_for(text),
                          on_sentence=on_sentence, on_token=on_token)

    def _task_for(self, text: str) -> Task:
        """Pick the routing task for a freeform utterance. Open knowledge
        questions (science, 'why/how does…', web research) → OPEN_QA (gpt-5.5
        first); everything else → COMMAND (the fast on-device 1.5B)."""
        t = text.strip()
        if _OPEN_QA.search(t):
            return Task.OPEN_QA
        if t.endswith("?") and len(t.split()) >= 4:
            return Task.OPEN_QA
        return Task.COMMAND

    def _remember(self, text: str, lang: str, *, now: datetime) -> Reply:
        body = _REMEMBER.sub("", text, count=1).strip(" ,.:!")
        body = _REMEMBER_TAIL.sub("", body).strip()
        if not body:
            return Reply(_t(lang, "Co mam zapamiętać?", "What should I remember?"), lang, "wake")
        title, content = _split_title_content(body)
        if title:
            note = self.memory.add_note(content, now=now, title=title)
            spoken = title  # read the title back, not a long dictated body
        else:
            note = self.memory.add_note(body, now=now)
            spoken = note.text
        self._embed_note(note)
        return Reply(
            _t(lang, f"Zapamiętałam: {spoken}.", f"Got it, I'll remember: {spoken}."),
            lang, "note", {"id": note.id, "title": note.title, "text": note.text},
        )

    def _set_name(self, text: str, lang: str, *, now: datetime) -> Reply:
        m = _NAME_SET.search(text)
        name = m.group(1).strip() if m else ""
        if not name:
            return Reply(_t(lang, "Jak mam Cię nazywać?", "What should I call you?"), lang, "wake")
        name = name[:1].upper() + name[1:]
        self.memory.set_profile("name", name, now=now)
        return Reply(
            _t(lang, f"Miło mi, {name}! Zapamiętam.", f"Nice to meet you, {name}! I'll remember."),
            lang, "profile", {"name": name},
        )

    def _get_name(self, lang: str) -> Reply:
        name = self.memory.get_profile("name")
        if not name:
            return Reply(
                _t(lang, "Jeszcze nie znam Twojego imienia. Powiedz „mam na imię…”.",
                   "I don't know your name yet. Say \"my name is…\"."),
                lang, "profile", {"name": None},
            )
        return Reply(_t(lang, f"Masz na imię {name}.", f"Your name is {name}."), lang, "profile", {"name": name})

    def _say_time(self, lang: str, now: datetime) -> Reply:
        """Tell the current time from the (NTP-synced) system clock — no network."""
        data = {"hour": now.hour, "minute": now.minute}
        if lang == "pl":
            # Spell the time out in Polish words so Piper says it naturally
            # ("dwudziesta trzecia szesnaście") instead of mangling "23:16".
            from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.plnum import time_words

            return Reply(
                f"Jest godzina {time_words(now.hour, now.minute)}.", lang, "time", data
            )
        h12 = now.hour % 12 or 12
        ampm = "AM" if now.hour < 12 else "PM"
        return Reply(f"The time is {h12}:{now.minute:02d} {ampm}.", lang, "time", data)

    def _say_date(self, lang: str, now: datetime) -> Reply:
        """Tell today's date with locale-independent day/month names."""
        wd = now.weekday()
        data = {"year": now.year, "month": now.month, "day": now.day, "weekday": wd}
        if lang == "pl":
            text = f"Dziś jest {_PL_DAYS[wd]}, {now.day} {_PL_MONTHS[now.month - 1]} {now.year}."
        else:
            text = f"Today is {_EN_DAYS[wd]}, {_EN_MONTHS[now.month - 1]} {now.day}, {now.year}."
        return Reply(text, lang, "date", data)

    def _record_voice_note(self, lang: str) -> Reply:
        # The engine only routes; the runner performs the actual capture + save
        # (audio I/O is a side effect, kept out of the pure engine).
        return Reply(
            _t(lang, "Słucham — naciśnij przycisk i nagraj notatkę.",
               "Go ahead — press the button and record your note."),
            lang, "voice_note_record", {},
        )

    def _play_voice_notes(self, lang: str) -> Reply:
        notes = self.memory.voice_notes()
        if not notes:
            return Reply(
                _t(lang, "Nie masz nagranych notatek głosowych.", "You have no voice notes."),
                lang, "voice_note_play", {"paths": []},
            )
        paths = [n.audio_path for n in notes[-5:]]
        n = len(paths)
        if lang == "pl":
            word = "notatkę" if n == 1 else "notatki" if 2 <= n <= 4 else "notatek"
            text = f"Odtwarzam {n} {word} głosowe." if n != 1 else "Odtwarzam ostatnią notatkę głosową."
        else:
            text = f"Playing {n} voice note{'s' if n != 1 else ''}."
        return Reply(text, lang, "voice_note_play", {"paths": paths})

    def _remind(self, text: str, lang: str, *, now: datetime) -> Reply:
        when = parse_when(text, now)
        category = "alarm" if _ALARM_HINT.search(text) else "event" if _EVENT_HINT.search(text) else "reminder"
        # Strip the trigger verb, the time expression, then leftover filler.
        task = _REMIND.sub("", text, count=1)
        task = _TIME_SPAN.sub("", task)
        task = _strip_lead_filler(task)
        if not when:
            return Reply(
                _t(lang, "Na kiedy? Np. „o 15:00” albo „za 10 minut”.",
                   "When? E.g. \"at 3pm\" or \"in 10 minutes\"."),
                lang, "wake",
            )
        if not task:
            task = {
                "alarm": _t(lang, "budzik", "alarm"),
                "event": _t(lang, "wydarzenie", "event"),
            }.get(category, _t(lang, "przypomnienie", "reminder"))
        rem = self.memory.add_reminder(task, due=when, now=now, category=category)
        hhmm = when.strftime("%H:%M")
        lead = {
            "alarm": _t(lang, f"Ustawiłam budzik na {hhmm}", f"Alarm set for {hhmm}"),
            "event": _t(lang, f"Zapisałam na {hhmm}", f"Saved for {hhmm}"),
        }.get(category, _t(lang, f"Przypomnę Ci o {hhmm}", f"I'll remind you at {hhmm}"))
        return Reply(
            f"{lead}: {task}.",
            lang, "reminder", {"id": rem.id, "due": rem.due, "text": task, "category": category},
        )

    def _recall_notes(self, text: str, lang: str) -> Reply:
        notes = self.memory.notes()
        if not notes:
            return Reply(_t(lang, "Nic jeszcze nie zapisałam.", "I haven't noted anything yet."), lang, "recall")
        lines = "; ".join(n.text for n in notes[-10:])
        return Reply(
            _t(lang, f"Pamiętam: {lines}.", f"I remember: {lines}."),
            lang, "recall", {"count": len(notes)},
        )

    def _recall_reminders(self, lang: str) -> Reply:
        pend = self.memory.pending()
        if not pend:
            return Reply(_t(lang, "Nie masz żadnych przypomnień.", "You have no reminders."), lang, "recall")
        lines = "; ".join(
            f"{datetime.fromisoformat(r.due).strftime('%H:%M')} — {r.text}" for r in pend[:10]
        )
        return Reply(
            _t(lang, f"Twoje przypomnienia: {lines}.", f"Your reminders: {lines}."),
            lang, "recall", {"count": len(pend)},
        )

    def _weather(self, text: str, lang: str) -> Reply:
        """Current conditions via Open-Meteo. Default location is Kraków."""
        place = self._weather_place(text, lang)
        try:
            c = self.weather.current(place)
        except WeatherError as e:
            log.warning("weather lookup failed (%s)", e)
            return Reply(
                _t(lang, "Nie mogę teraz sprawdzić pogody.", "I can't check the weather right now."),
                lang, "error", {"reason": "weather_failed"},
            )
        desc = describe_code(c.code, lang)
        temp, feels, wind = round(c.temperature), round(c.feels_like), round(c.wind_speed)
        if lang == "pl":
            # Label form ("Kraków: …") avoids Polish locative inflection
            # ("w Krakowie") that a bare nominative ("w Kraków") would get wrong.
            out = (f"{c.place}: {temp}{c.units_temp}, {desc}. "
                   f"Odczuwalna {feels}{c.units_temp}, wiatr {wind} {c.units_wind}.")
        else:
            out = (f"In {c.place} it's {temp}{c.units_temp}, {desc}. "
                   f"Feels like {feels}{c.units_temp}, wind {wind} {c.units_wind}.")
        return Reply(out, lang, "weather", {"place": c.place, "temp": temp, "code": c.code})

    def _weather_place(self, text: str, lang: str) -> Place | None:
        """Pull a city out of the question, or ``None`` to use the default (Kraków)."""
        m = _WEATHER_CITY.search(text.strip())
        if not m:
            return None
        city = _tidy(m.group(1))
        # Drop trailing time words so "pogoda w Krakowie dziś" still resolves.
        city = re.sub(r"\s+(dziś|dzis|dzisiaj|teraz|today|now)$", "", city, flags=re.IGNORECASE)
        if not city or city.lower() in {"dziś", "dzis", "dzisiaj", "teraz", "today", "now"}:
            return None
        try:
            return self.weather.geocode(city, lang)  # None → default location
        except WeatherError:
            return None

    def _news(self, lang: str) -> Reply:
        """Top headlines from international agencies, focused on Kraków + Poland.

        Primary path is Gemini search-grounding (fresh, Kraków-focused, translated
        to the user's language). If Gemini is absent or errors (quota/billing),
        fall back to a keyless RSS brief (Poland-focused Polish feeds for `pl`,
        international agencies for `en`).
        """
        if self.gemini.available:
            if lang == "pl":
                query = ("Podaj najważniejsze aktualne wiadomości z międzynarodowych agencji "
                         "(Reuters, AP, AFP, BBC), ze szczególnym uwzględnieniem Krakowa i Polski.")
                sys = self.persona + (" Odpowiedz po polsku, zwięźle, 3-4 najważniejsze punkty. "
                                      "Przetłumacz nagłówki na polski.")
            else:
                query = ("Give the top current news from international agencies "
                         "(Reuters, AP, AFP, BBC), focusing on Kraków and Poland.")
                sys = self.persona + " Answer in English, briefly — 3-4 key points."
            try:
                answer = self.gemini.grounded(query, system=sys)
                return Reply(answer, lang, "news", {"grounded": True, "focus": "krakow-poland"})
            except GeminiError as e:
                log.warning("news via Gemini failed (%s); falling back to RSS", e)
        # Keyless RSS fallback — no API key, no LLM.
        try:
            items = self.news.headlines(lang)
        except NewsError as e:
            log.warning("RSS news fallback failed (%s)", e)
            items = []
        if items:
            lead = _t(lang, "Najważniejsze wiadomości:", "Top headlines:")
            body = " ".join(f"{i + 1}. {h}." for i, h in enumerate(items))
            return Reply(f"{lead} {body}", lang, "news", {"source": "rss", "count": len(items)})
        return Reply(
            _t(lang, "Nie mogę teraz sprawdzić wiadomości.", "I can't check the news right now."),
            lang, "error", {"reason": "news_unavailable"},
        )

    def _radio(self, text: str, lang: str) -> Reply:
        """Play a named station, or offer the headline stations if none is named."""
        if not self.radio.available:
            return Reply(
                _t(lang, "Radio nie jest skonfigurowane.", "Radio isn't configured."),
                lang, "radio_offer", {},
            )
        station = self.radio.resolve(text)
        if station is None:
            names = [s.name for s in self.radio.offer()]
            if len(names) > 1:
                joined = ", ".join(names[:-1]) + _t(lang, f" lub {names[-1]}", f" or {names[-1]}")
            else:
                joined = names[0]
            return Reply(
                _t(lang, f"Mogę włączyć: {joined}. Którą stację?",
                   f"I can play: {joined}. Which station?"),
                lang, "radio_offer", {"stations": [s.id for s in self.radio.offer()]},
            )
        # "Włączam stację <name>" (apposition) keeps the name in the nominative
        # so single-word channel nicknames don't need accusative inflection
        # ("Włączam Trójka" → grammatically "Trójkę"); works for every station.
        return Reply(
            _t(lang, f"Włączam stację {station.name}.", f"Playing {station.name}."),
            lang, "radio_play", {"id": station.id, "name": station.name, "url": station.url},
        )

    def _radio_stop(self, lang: str) -> Reply:
        return Reply(
            _t(lang, "Wyłączam radio.", "Turning off the radio."), lang, "radio_stop", {},
        )

    def _lookup(self, query: str, lang: str) -> Reply:
        """Open/summarise a site or answer a general up-to-date query via Gemini."""
        if not self.gemini.available:
            return Reply(
                _t(lang,
                   "To wymaga konta Gemini — ustaw GEMINI_API_KEY.",
                   "That needs a Gemini account — set GEMINI_API_KEY."),
                lang, "news", {"needs_key": True},
            )
        sys = self.persona + _t(lang, " Streść zwięźle, po polsku.", " Summarise briefly, in English.")
        try:
            answer = self.gemini.grounded(query, system=sys)
        except GeminiError as e:
            log.warning("site lookup via Gemini failed (%s)", e)
            return Reply(
                _t(lang, "Nie mogę teraz tego sprawdzić.", "I can't look that up right now."),
                lang, "error", {"reason": "lookup_failed"},
            )
        return Reply(answer, lang, "news", {"grounded": True})

    def _chat(
        self,
        text: str,
        lang: str,
        *,
        task: Task = Task.COMMAND,
        on_sentence: OnSentence | None = None,
        on_token: OnToken | None = None,
    ) -> Reply:
        # Personalise with the user's name when known.
        system = self.persona
        name = self.memory.get_profile("name")
        if name:
            system += _t(lang, f" Użytkownik ma na imię {name}.", f" The user's name is {name}.")
        # Retrieve the user's relevant stored notes and inject them. Shared
        # `system=` kwarg → this covers every backend uniformly.
        system += self._notes_context(text, lang)
        # Router path (production): try each available backend for this task in the
        # configured order — COMMAND→1.5B, RECOMMEND→4.5B, OPEN_QA→gpt-5.5, all
        # preferring the 11B-Ollama when reachable. Falls through to Gemini/canned.
        if self.router is not None:
            reply = self._route_chat(text, system, lang, task, on_sentence, on_token)
            if reply is not None:
                return reply
        else:
            # Legacy single-backend chain (unit tests inject llm/openai directly,
            # no router). Local-first — OpenAI is only a fallback here; the new
            # policy reserves the cloud for OPEN_QA, which the router handles.
            if self.llm is not None and self.llm.available:
                if on_sentence is not None:
                    return self._chat_stream_backend(self.llm, "local", text, system, lang, on_sentence, on_token)
                try:
                    return Reply(self.llm.chat(text, system=system), lang, "chat", {"engine": "local"})
                except LlmError as e:
                    log.warning("local model failed (%s) — trying OpenAI/Gemini", e)
            if self.openai is not None and self.openai.available:
                try:
                    return Reply(self.openai.chat(text, system=system), lang, "chat", {"engine": "openai"})
                except OpenAiError as e:
                    log.warning("OpenAI fallback failed (%s)", e)
        if self.gemini.available:
            try:
                answer = self.gemini.chat(text, system=system)
            except GeminiError as e:
                return Reply(_t(lang, f"Coś poszło nie tak: {e}", f"Something went wrong: {e}"), lang, "error")
            return Reply(answer, lang, "chat", {"engine": "gemini"})
        return Reply(
            _t(lang,
               "Mogę rozmawiać swobodnie po ustawieniu GEMINI_API_KEY (albo lokalnego modelu).",
               "I can chat freely once GEMINI_API_KEY (or a local model) is set."),
            lang, "chat", {"needs_key": True},
        )

    def _route_chat(
        self,
        text: str,
        system: str,
        lang: str,
        task: Task,
        on_sentence: OnSentence | None,
        on_token: OnToken | None,
    ) -> Reply | None:
        """Try each available backend for ``task`` in order; return the first
        success, or ``None`` so ``_chat`` falls through to Gemini/canned. A
        backend that fails BEFORE emitting anything is skipped for the next."""
        for name, backend in self.router.route(task):  # type: ignore[union-attr]
            try:
                if on_sentence is not None:
                    return self._chat_stream_backend(
                        backend, name, text, system, lang, on_sentence, on_token, task=task)
                answer = backend.chat(text, system=system)
                return Reply(answer, lang, "chat", {"engine": name, "task": task.value})
            except (LlmError, OpenAiError) as e:
                log.warning("backend %s failed for %s (%s) — trying next", name, task.value, e)
                continue
        return None

    def _chat_stream_backend(
        self,
        backend: Llm,
        name: str,
        text: str,
        system: str,
        lang: str,
        on_sentence: OnSentence,
        on_token: OnToken | None,
        *,
        task: Task | None = None,
    ) -> Reply:
        """Stream ``backend``, slicing into sentences for immediate TTS.

        Each completed sentence is handed to ``on_sentence`` the moment it ends,
        so the runner can start speaking while later tokens generate. If the
        backend fails BEFORE any token, the error propagates so the router can try
        the next backend; a failure mid-stream keeps the partial (already-spoken)
        reply. ``data["streamed"]=True`` tells the caller not to re-speak.
        """
        slicer = SentenceSlicer()
        parts: list[str] = []
        seen_token = False
        try:
            for chunk in backend.chat_stream(text, system=system):
                if not seen_token:
                    seen_token = True
                    if on_token is not None:
                        on_token()
                parts.append(chunk)
                for sentence in slicer.feed(chunk):
                    on_sentence(sentence, lang)
            for sentence in slicer.flush():
                on_sentence(sentence, lang)
        except (LlmError, OpenAiError):
            if not seen_token:
                raise  # nothing spoken yet → let the router fall to the next backend
            log.warning("backend %s failed mid-stream — keeping partial reply", name)
        data = {"engine": name, "streamed": True}
        if task is not None:
            data["task"] = task.value
        return Reply("".join(parts).strip(), lang, "chat", data)

    # -- reminders due -------------------------------------------------
    def due_reminders(self, now: datetime) -> list[Reply]:
        """Fire any due reminders as spoken replies (call on a timer tick)."""
        out: list[Reply] = []
        for r in self.memory.due(now):
            cat = getattr(r, "category", "reminder")
            lead = {"alarm": "Budzik", "event": "Wydarzenie"}.get(cat, "Przypominam")
            out.append(
                Reply(f"{lead}: {r.text}.", "pl", "reminder",
                      {"id": r.id, "fired": True, "category": cat}),
            )
        return out
