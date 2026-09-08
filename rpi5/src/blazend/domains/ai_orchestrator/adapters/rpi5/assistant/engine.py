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
from datetime import datetime, timedelta
from typing import Any

from blazend.domains.ai_orchestrator.adapters.rpi5.assistant import wake
from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.gemini import GeminiClient, GeminiError
from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.news import NewsClient, NewsError
from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.openai import OpenAiClient, OpenAiError
from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.prompts import (
    PromptLibrary,
    format_candidates,
    parse_choice,
)
from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.radio import RadioDirectory
from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.recommend import RecommendationEngine
from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.semantic import SemanticLibrary
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
from blazend.i18n import msg, plural, table

# Streaming hooks for sentence-sliced TTS. ``on_sentence(text, language)`` fires
# the instant a sentence completes; ``on_token()`` fires on the first token (for
# latency instrumentation). Both optional — absent → the old whole-reply path.
OnSentence = Callable[[str, str], None]
OnToken = Callable[[], None]

log = logging.getLogger("blazend.domains.ai_orchestrator.adapters.rpi5.assistant")

# Router backend names that leave the LAN. The memory context is withheld from
# these unless embeddings.yaml `notes_context.share_with_cloud: true` — Bieliks
# and mesh peers (mlx-*) are the user's own machines and always get it.
_CLOUD_ENGINES = frozenset({"gpt-5.5", "openai", "gemini"})

_PL_HINT = re.compile(r"[ąćęłńóśźż]|\b(jest|czy|co|jak|mi|że|nie|tak|godzina|przypomnij|zapamiętaj|wiadomości)\b", re.IGNORECASE)
_EN_HINT = re.compile(r"\b(the|what|is|remind|remember|news|please|time|tomorrow|at)\b", re.IGNORECASE)


def detect_lang(text: str) -> str:
    """Heuristic language detector. Polish-first per the persona."""
    if _PL_HINT.search(text):
        return "pl"
    if _EN_HINT.search(text):
        return "en"
    return "pl"


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
# Locale-independent day/month names live in the message catalog
# (tables.days / tables.months — don't depend on the system locale).
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
# Radio requires INTENT, not just a station name: overnight 2026-08-30 01:11 a
# garbled ambient transcript ("Dmyt, Trójka, Wojkowe, Ciachowe…") inside a
# false-wake window matched the bare station stem and started Trójka unasked.
# A play happens only with an action verb, or when the whole utterance is a
# short direct ask ("dżesika, trójka" ≤ 3 words).
_RADIO_VERB = re.compile(
    r"\b(w(ł|l)(ą|a)cz\w*|pu(ś|s)(ć|c)|zagraj|graj|prze(ł|l)(ą|a)cz\w*|nastaw|"
    r"daj|pos(ł|l)ucham\w*|play|turn\s+on|switch|put\s+on|tune)\b",
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
# Recommendation ("poleć coś", "co warto przeczytać") vs direct play ("zagraj X",
# fast-path). A recommendation reaches the brain (nlu.miss) and runs the RAG.
_RECOMMEND = re.compile(
    r"\b(pole(ć|c)|polecisz|poleci(ł|l)ab?y?(ś|s)?|zasugeruj|zaproponuj|doradz(isz|(ł|l))|"
    r"co\s+(mi\s+)?(polecasz|proponujesz|byś\s+poleci(ł|l)a)|"
    r"co\s+warto\s+(przeczyta(ć|c)|pos(ł|l)ucha(ć|c))|"
    r"masz\s+(jak(ą|a)(ś|s)|co(ś|s))\s+(dobr\w+\s+)?(do\s+(czytania|s(ł|l)uchania)|ksi(ą|a)(ż|z)k\w*)|"
    r"recommend|suggest\s+(a\s+|some)?(book|music|something)|what\s+should\s+i\s+(read|listen))\b",
    re.IGNORECASE,
)
# List / browse the library ("jakie masz książki", "co masz do czytania").
_LIST_BOOKS = re.compile(
    r"\b(jakie\s+(masz|s(ą|a)\s+dost(ę|e)pne)\s+(ksi(ą|a)(ż|z)k\w*|audiobook\w*|lektur\w*)|"
    r"co\s+masz\s+do\s+(czytania|s(ł|l)uchania)|jak(ą|a)\s+masz\s+bibliotek\w*|"
    r"ile\s+masz\s+(ksi(ą|a)(ż|z)ek|audiobook(ó|o)w)|"
    r"(list|what)\s+(of\s+)?(books|audiobooks)|what\s+can\s+i\s+(read|listen))\b",
    re.IGNORECASE,
)
# Music vs book context for a recommendation.
_MUSIC_CTX = re.compile(r"\b(muzyk\w*|piosenk\w*|utw(ó|o)r\w*|kaw(a|ą)(ł|l)\w*|music|song|track)\b", re.IGNORECASE)
_BOOK_CTX = re.compile(r"\b(ksi(ą|a)(ż|z)k\w*|audiobook\w*|lektur\w*|powie(ś|s)(ć|c)\w*|czyta\w*|book|read)\b", re.IGNORECASE)
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


def _looks_ambient(text: str) -> bool:
    """Heuristic: a transcript that reached us asleep and wake-less but reads as
    mid-stream prose is overheard TV/radio, not the user (a real attempt whose
    wake word whisper dropped is a short imperative — "włącz trójkę"). Ambient
    tells: many words, or a sentence boundary inside the capture window."""
    words = text.split()
    if len(words) >= 9:
        return True
    inner = text.strip().rstrip(".?!…")
    return any(mark in inner for mark in (". ", "? ", "! "))


# Bare stop orders (with/without diacritics — whisper drops them at times).
# Kept in sync with the ASR sentinel's _STOP_TOKENS/_STOP_PHRASES.
_STOP_ORDERS = frozenset({
    "stop", "przestań", "przestan", "dość", "dosc", "cisza", "koniec",
    "pauza", "wyłącz", "wylacz", "zamknij się", "zamknij sie",
    # EN asset parity (docs/13): retained though runtime is PL-only.
    "quiet", "silence", "enough", "shut up", "be quiet",
})


def _is_stop_order(text: str) -> bool:
    """True for a short, unambiguous stop command ("stop", "przestań", …) —
    the one class of utterance that must route even asleep and wake-less."""
    cleaned = re.sub(r"[^\w\s]", " ", text.lower()).strip()
    if not cleaned or len(cleaned.split()) > 3:
        return False
    return cleaned in _STOP_ORDERS or any(
        tok in _STOP_ORDERS for tok in cleaned.split())


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
        recommender: RecommendationEngine | None = None,
        prompts: PromptLibrary | None = None,
        persona: str | None = None,
        always_awake: bool = False,
        wake_window_s: float = 20.0,
        history_turns: int = 6,
        notes_top_k: int = 4,
        notes_min_score: float = 0.82,
        notes_rel_margin: float = 0.06,
        notes_max_chars: int = 1200,
        notes_include_voice: bool = True,
        notes_share_with_cloud: bool = False,
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
        self._notes_include_voice = notes_include_voice
        self._notes_share_with_cloud = notes_share_with_cloud
        # The Jessica persona (same text as jessica_core::DEFAULT_PERSONA), from
        # the message catalog. Always the Polish text — the persona itself
        # instructs the model to answer in the user's language.
        self.persona = persona if persona is not None else msg("prompts.persona", "pl")
        self.awake = always_awake
        self._always_awake = always_awake
        # Conversation window (mirrors wake-word.yaml conversation_window_s):
        # the engine is awake until this deadline; every wake — REAL
        # (notify_wake, from wake.detected) or textual — re-arms it, and every
        # acted-on utterance extends it for follow-ups. Before 2026-08-29 the
        # engine woke ONLY on the wake word appearing verbatim in the
        # transcript, so whisper mangling "dżesika" ("Jeszcze karp…") produced
        # an absurd "I'm asleep — say Jessica" right after the user said it.
        self._wake_window_s = wake_window_s
        self._awake_until: datetime | None = None
        # Conversation history (2026-08-30): rolling (user, assistant) CHAT
        # turns fed back to the LLM so multi-turn talk keeps its thread. Tool
        # commands (radio/time/notes) never enter it, and it clears when the
        # conversation window expires — a new wake is a new topic.
        self._history_turns = max(1, history_turns)
        self._chat_history: list[tuple[str, str]] = []
        self._history_until: datetime | None = None
        # Book/music recommendation RAG (semantic + ontology). Injected in tests;
        # built lazily otherwise (loads the semantic index only on first use).
        self._recommender = recommender
        self._recommender_built = recommender is not None
        self._prompts = prompts or PromptLibrary()
        if embedder is not None and embedder.available:
            self._backfill_embeddings()

    @property
    def recommender(self) -> RecommendationEngine | None:
        """The RAG recommender, built on first use (None if no semantic index)."""
        if not self._recommender_built:
            self._recommender_built = True
            try:
                sem = SemanticLibrary()
                self._recommender = RecommendationEngine(semantic=sem) if sem.available else None
            except Exception:  # noqa: BLE001 — no index / no numpy → no recommendations
                self._recommender = None
        return self._recommender

    # -- semantic memory embeddings (text notes + voice-memo transcripts) --
    def _backfill_embeddings(self) -> None:
        """Embed memories that lack a stored vector. Cheap when nothing is
        missing, so callers may re-run it any time — the store revalidates
        against the file's mtime, which makes memos recorded by OTHER
        processes (ASR/orchestrator) searchable here without a restart."""
        if not (self._embedder and self._embedder.available):
            return
        for item in self.memory.items_missing_embeddings(model=self._embedder_model):
            blob = f"{item.title}. {item.text}" if item.title else item.text
            try:
                vector = self._embedder.embed([blob], kind="passage")[0]
            except EmbedderError as e:
                log.warning("memory embedding failed (%s); semantic recall degraded", e)
                return
            self.memory.set_note_embedding(item.id, vector, model=self._embedder_model)

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

    # A single voice-memo transcript can be a minute of speech — cap what one
    # hit may spend of the prompt budget so it can't crowd out the others.
    _VOICE_HIT_CHARS = 200

    def _notes_context(self, text: str, lang: str) -> str:
        """Retrieve the user's memories (notes + voice memos) relevant to
        ``text`` for the LLM prompt — the local search whose results become
        context for EVERY backend, external models included."""
        if not (self._embedder and self._embedder.available):
            return ""
        self._backfill_embeddings()  # memos written by other processes join here
        try:
            qvec = self._embedder.embed([text], kind="query")[0]
        except EmbedderError:
            return ""
        hits = self.memory.search_memory_semantic(
            qvec,
            limit=self._notes_top_k,
            min_score=self._notes_min_score,
            rel_margin=self._notes_rel_margin,
        )
        if not hits:
            return ""
        header = msg("prompts.notes_context.header", lang)
        voice_tag = msg("prompts.notes_context.voice_tag", lang)
        budget = self._notes_max_chars
        parts: list[str] = []
        for h in hits:
            if h.kind == "voice":
                if not self._notes_include_voice:
                    continue
                body = h.text[: self._VOICE_HIT_CHARS].rstrip()
                if len(h.text) > self._VOICE_HIT_CHARS:
                    body += "…"
                piece = f" {voice_tag} {body}"
            else:
                piece = f" [{h.title}] {h.text}" if h.title else f" {h.text}"
            if len(piece) > budget:
                break
            parts.append(piece)
            budget -= len(piece)
        return header + "".join(parts) if parts else ""

    # -- top-level -----------------------------------------------------
    def notify_wake(self, now: datetime) -> None:
        """The REAL wake fired (a ``wake.detected`` event) — open/extend the
        conversation window regardless of what whisper later makes of the
        audio. The brain feeds these from ``wake.sock``."""
        if self._always_awake:
            return
        self.awake = True
        self._awake_until = now + timedelta(seconds=self._wake_window_s)

    # Extra window granted after a spoken reply: the window is counted from
    # route ENTRY (the injected `now` keeps the engine clock-free and
    # testable), but the follow-up answer can only start once generation AND
    # TTS finish — tens of seconds later. Without this allowance her OWN
    # follow-up got the sleep notice (live 2026-08-31 08:42). 90 s covers a
    # slow generation (~60 s) + speech (~20 s) + the capture lead-in.
    _REPLY_ALLOWANCE_S = 90.0

    def _extend_awake(self, now: datetime, extra_s: float = 0.0) -> None:
        if not self._always_awake:
            self.awake = True
            self._awake_until = now + timedelta(seconds=self._wake_window_s + extra_s)

    def _expire_awake(self, now: datetime) -> None:
        if self._always_awake:
            return
        if self._awake_until is not None and now > self._awake_until:
            self.awake = False
            self._awake_until = None
            # NOTE: the chat history deliberately SURVIVES window expiry —
            # clearing it here wiped the thread when a stray utterance hit an
            # expired window mid-conversation (live 2026-08-31 08:53: an
            # expletive aside cleared the topic and the next question got
            # "what organisation do you mean?"). History has its own TTL.

    _HISTORY_MAX_CHARS = 2400
    # The thread outlives the awake window: a re-wake within this TTL
    # continues the topic; longer silence starts fresh.
    _HISTORY_TTL_S = 600.0

    def _remember_turn(self, user: str, assistant: str) -> None:
        """Append one chat exchange and trim oldest-first to the turn cap and
        a hard character budget (small local models choke on huge prompts)."""
        self._chat_history.append((user, assistant))
        while len(self._chat_history) > self._history_turns:
            self._chat_history.pop(0)
        while (len(self._chat_history) > 1
               and sum(len(u) + len(a) for u, a in self._chat_history)
               > self._HISTORY_MAX_CHARS):
            self._chat_history.pop(0)

    def engages(self, text: str) -> bool:
        """True when :meth:`route` would reach a real command handler (possibly a
        slow LLM call) — i.e. not asleep, not empty, not a bare wake. Pure probe
        (no state change), so a caller can emit a "thinking" cue BEFORE the
        potentially slow route() without double-routing."""
        text = text.strip()
        if not text:
            return False
        if wake.is_wake(text):
            command = wake.strip_wake(text)
            return bool(command) and not (command == text and wake.is_wake(command))
        self._expire_awake(datetime.now())
        return self.awake

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

        self._expire_awake(now)
        if (self._history_until is not None and now > self._history_until
                and self._chat_history):
            self._chat_history.clear()  # topic went stale — fresh thread
        if self._chat_history:
            self._history_until = now + timedelta(seconds=self._HISTORY_TTL_S)
        named = wake.is_wake(text)
        if named:
            self._extend_awake(now)
            command = wake.strip_wake(text)
            if not command or command == text and wake.is_wake(command):
                command = ""
        elif self.awake:
            command = text
        elif _is_stop_order(text):
            # Wake-less STOP (2026-09-08): the ASR stop sentinel routes a bare
            # "stop" heard over a playing stream, with no wake and possibly no
            # awake window. Stopping must NEVER be gated on being awake — the
            # user could not silence a mis-started radio because "Dżesika"
            # scored under the wake threshold against the stream's mic duck.
            command = text
        else:
            if _looks_ambient(text):
                # Overheard TV/radio after a false wake — answering the sleep
                # notice to a television is noise. Stay silent.
                return Reply("", lang, "asleep")
            return Reply(msg("wake.asleep", lang), lang, "asleep")

        if not command:
            name = self.memory.get_profile("name")
            if name:
                return Reply(msg("wake.greet_name", lang, name=name), lang, "wake")
            return Reply(msg("wake.greet", lang), lang, "wake")

        reply = self._handle(command, lang, now=now, on_sentence=on_sentence,
                             on_token=on_token, named=named)
        # An acted-on utterance re-opens the follow-up window (mirrors the
        # orchestrator's conversation-window semantics) with the reply
        # allowance on top — see _REPLY_ALLOWANCE_S.
        if reply.action != "asleep":
            self._extend_awake(now, extra_s=self._REPLY_ALLOWANCE_S)
            if self._chat_history:
                self._history_until = now + timedelta(seconds=self._HISTORY_TTL_S)
        return reply

    # -- command handling ----------------------------------------------
    def _handle(
        self,
        text: str,
        lang: str,
        *,
        now: datetime,
        on_sentence: OnSentence | None = None,
        on_token: OnToken | None = None,
        named: bool = True,
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
        if _LIST_BOOKS.search(text):
            return self._list_books(lang)
        if _RECOMMEND.search(text):
            return self._recommend(text, lang)
        if _RADIO_STOP.search(text):
            return self._radio_stop(lang)
        if _RADIO_KW.search(text) or self.radio.resolve(text) is not None:
            # Intent gate (see _RADIO_VERB): an action verb, or a short direct
            # ask. Ambient babble that merely CONTAINS a station name falls
            # through to the normal routes instead of starting a stream.
            if _RADIO_VERB.search(text) or len(text.split()) <= 3:
                return self._radio(text, lang)
        # A short bare phrase that maps to library signals ("francuska klasyka",
        # "baśń dla dzieci", "poezja") is a recommendation ask, not open chat —
        # route it to the RAG so the model picks from the shelf instead of rambling.
        rec = self.recommender
        if (rec is not None and not _OPEN_QA.search(text) and len(text.split()) <= 5
                and rec.ontology.parse(text).any):
            return self._recommend(text, lang)
        if not named and not self._chat_history and _looks_ambient(text):
            # Chat-fallback ambient guard (live 2026-08-30 18:44): a false
            # wake let overheard room conversation reach the LLM, which
            # replied into a private talk and armed a follow-up chain. Prose
            # with no wake token and no established conversation never seeds
            # a chat; real commands (notes, radio, time…) routed above.
            return Reply("", lang, "asleep")
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

    # -- recommendations (multi-layer RAG) -----------------------------
    def _recommend_kinds(self, text: str) -> tuple[str, ...]:
        music, book = bool(_MUSIC_CTX.search(text)), bool(_BOOK_CTX.search(text))
        if music and not book:
            return ("music",)
        if book and not music:
            return ("book",)
        return ("book", "music")

    def _recommend(self, text: str, lang: str) -> Reply:
        """Suggest a book/track: ontology + semantic retrieval → LLM reasoning
        (RECOMMEND task, DSPy-compiled prompt) → spoken pitch. Falls back to a
        template pick when no model is reachable."""
        rec = self.recommender
        if rec is None:
            return Reply(msg("recommend.no_library", lang), lang, "recommend")
        kinds = self._recommend_kinds(text)
        cands = rec.candidates(text, kinds=kinds, k=6)
        if not cands:
            return Reply(msg("recommend.no_match", lang), lang, "recommend")
        reasoned = self._reason_recommend(text, cands, lang, kinds)
        if reasoned is not None:
            return reasoned
        return self._recommend_reply(cands[0], "", lang)  # template fallback

    def _reason_recommend(self, text: str, cands: list[dict[str, Any]], lang: str,
                          kinds: tuple[str, ...]) -> Reply | None:
        """Run the compiled recommendation prompt through the RECOMMEND backends
        (Bielik 4.5B / 11B). Returns None if no backend answered."""
        if self.router is None:
            return None
        name = "music_recommendation" if kinds == ("music",) else "book_recommendation"
        system, user = self._prompts.render(name, query=text, candidates=format_candidates(cands))
        for backend_name, backend in self.router.route(Task.RECOMMEND):
            try:
                raw = backend.chat(user, system=system)
            except (LlmError, OpenAiError) as e:
                log.warning("recommend backend %s failed (%s) — next", backend_name, e)
                continue
            idx, pitch = parse_choice(raw, len(cands))
            return self._recommend_reply(cands[idx], pitch, lang, engine=backend_name)
        return None

    def _recommend_reply(self, item: dict[str, Any], pitch: str, lang: str, *, engine: str = "template") -> Reply:
        """Build the spoken recommendation, naming the title (so the user can then
        say "zagraj <title>"). No auto-play — a suggestion is spoken, not started."""
        title, who = str(item.get("title", "")), str(item.get("who", ""))
        lead = (msg("recommend.lead_who", lang, title=title, who=who) if who
                else msg("recommend.lead", lang, title=title))
        # If the model already named the book, use its pitch alone; else prepend.
        text = pitch if (pitch and title and title.lower() in pitch.lower()) else (lead + pitch).strip()
        if not pitch:
            text = lead.strip() + msg("recommend.play_hint", lang)
        return Reply(text, lang, "recommend",
                     {"title": title, "who": who, "kind": item.get("type"), "engine": engine})

    def _list_books(self, lang: str) -> Reply:
        """Point the user at the library — with 1500+ audiobooks, guide by ask
        rather than enumerate (voice-first)."""
        rec = self.recommender
        n = rec.semantic.count("book") if rec is not None else 0
        if not n:
            return Reply(msg("book.offer_none", lang), lang, "book_offer")
        return Reply(msg("book.library_offer", lang, n=n), lang, "book_offer", {"count": n})

    def _remember(self, text: str, lang: str, *, now: datetime) -> Reply:
        body = _REMEMBER.sub("", text, count=1).strip(" ,.:!")
        body = _REMEMBER_TAIL.sub("", body).strip()
        if not body:
            return Reply(msg("memory.remember_what", lang), lang, "wake")
        # The ASR keeps a claimable clip of this very utterance — a claimed
        # memory is stored as sound + text (VoiceNote), else a plain text note.
        claimed = self.memory.claim_last_clip(body)
        if claimed is not None:
            vn = self.memory.add_voice_note(
                claimed[0], now=now, duration_s=claimed[1], transcript=body)
            self._backfill_embeddings()  # make it searchable immediately
            return Reply(
                msg("memory.remembered", lang, text=body),
                lang, "note", {"id": vn.id, "text": body, "audio_path": vn.audio_path},
            )
        title, content = _split_title_content(body)
        if title:
            note = self.memory.add_note(content, now=now, title=title)
            spoken = title  # read the title back, not a long dictated body
        else:
            note = self.memory.add_note(body, now=now)
            spoken = note.text
        self._embed_note(note)
        return Reply(
            msg("memory.remembered", lang, text=spoken),
            lang, "note", {"id": note.id, "title": note.title, "text": note.text},
        )

    def _set_name(self, text: str, lang: str, *, now: datetime) -> Reply:
        m = _NAME_SET.search(text)
        name = m.group(1).strip() if m else ""
        if not name:
            return Reply(msg("profile.ask_name", lang), lang, "wake")
        name = name[:1].upper() + name[1:]
        self.memory.set_profile("name", name, now=now)
        return Reply(
            msg("profile.name_saved", lang, name=name),
            lang, "profile", {"name": name},
        )

    def _get_name(self, lang: str) -> Reply:
        name = self.memory.get_profile("name")
        if not name:
            return Reply(msg("profile.name_unknown", lang), lang, "profile", {"name": None})
        return Reply(msg("profile.name_is", lang, name=name), lang, "profile", {"name": name})

    def _say_time(self, lang: str, now: datetime) -> Reply:
        """Tell the current time from the (NTP-synced) system clock — no network."""
        data = {"hour": now.hour, "minute": now.minute}
        if lang == "pl":
            # Spell the time out in Polish words so Piper says it naturally
            # ("dwudziesta trzecia szesnaście") instead of mangling "23:16".
            from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.plnum import time_words

            spoken = time_words(now.hour, now.minute)
        else:
            h12 = now.hour % 12 or 12
            ampm = "AM" if now.hour < 12 else "PM"
            spoken = f"{h12}:{now.minute:02d} {ampm}"
        return Reply(msg("time.now", lang, time=spoken), lang, "time", data)

    def _say_date(self, lang: str, now: datetime) -> Reply:
        """Tell today's date with locale-independent day/month names."""
        wd = now.weekday()
        data = {"year": now.year, "month": now.month, "day": now.day, "weekday": wd}
        text = msg("date.today", lang,
                   day_name=table("days", wd, lang),
                   day=now.day,
                   month=table("months", now.month, lang),
                   year=now.year)
        return Reply(text, lang, "date", data)

    def _record_voice_note(self, lang: str) -> Reply:
        # The engine only routes; the runner performs the actual capture + save
        # (audio I/O is a side effect, kept out of the pure engine).
        return Reply(msg("memo.record_prompt", lang), lang, "voice_note_record", {})

    def _play_voice_notes(self, lang: str) -> Reply:
        notes = self.memory.voice_notes()
        if not notes:
            return Reply(
                msg("memo.none_recorded", lang),
                lang, "voice_note_play", {"paths": []},
            )
        paths = [n.audio_path for n in notes[-5:]]
        text = plural("voice_notes_playing", len(paths), lang)
        return Reply(text, lang, "voice_note_play", {"paths": paths})

    def _remind(self, text: str, lang: str, *, now: datetime) -> Reply:
        when = parse_when(text, now)
        category = "alarm" if _ALARM_HINT.search(text) else "event" if _EVENT_HINT.search(text) else "reminder"
        # Strip the trigger verb, the time expression, then leftover filler.
        task = _REMIND.sub("", text, count=1)
        task = _TIME_SPAN.sub("", task)
        task = _strip_lead_filler(task)
        if not when:
            return Reply(msg("reminder.ask_when", lang), lang, "wake")
        if not task:
            task = table("reminder_labels", category, lang,
                         default=table("reminder_labels", "reminder", lang))
        rem = self.memory.add_reminder(task, due=when, now=now, category=category)
        hhmm = when.strftime("%H:%M")
        lead_key = {
            "alarm": "reminder.lead_alarm",
            "event": "reminder.lead_event",
        }.get(category, "reminder.lead_default")
        lead = msg(lead_key, lang, time=hhmm)
        return Reply(
            f"{lead}: {task}.",
            lang, "reminder", {"id": rem.id, "due": rem.due, "text": task, "category": category},
        )

    def _recall_notes(self, text: str, lang: str) -> Reply:
        notes = self.memory.notes()
        if not notes:
            return Reply(msg("memory.none_noted", lang), lang, "recall")
        lines = "; ".join(n.text for n in notes[-10:])
        return Reply(
            msg("memory.recall", lang, items=lines),
            lang, "recall", {"count": len(notes)},
        )

    def _recall_reminders(self, lang: str) -> Reply:
        pend = self.memory.pending()
        if not pend:
            return Reply(msg("reminder.none", lang), lang, "recall")
        lines = "; ".join(
            f"{datetime.fromisoformat(r.due).strftime('%H:%M')} — {r.text}" for r in pend[:10]
        )
        return Reply(
            msg("reminder.list", lang, items=lines),
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
                msg("weather.unavailable", lang),
                lang, "error", {"reason": "weather_failed"},
            )
        desc = describe_code(c.code, lang)
        temp, feels, wind = round(c.temperature), round(c.feels_like), round(c.wind_speed)
        # PL label form ("Kraków: …") avoids Polish locative inflection
        # ("w Krakowie") that a bare nominative ("w Kraków") would get wrong.
        out = msg("weather.conditions", lang, place=c.place, temp=temp,
                  units=c.units_temp, desc=desc, feels=feels, wind=wind,
                  wind_units=c.units_wind)
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
            query = msg("prompts.news_gemini_engine.query", lang)
            sys = msg("prompts.news_gemini.system", lang, persona=self.persona)
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
            lead = msg("news.headlines_lead", lang)
            body = " ".join(f"{i + 1}. {h}." for i, h in enumerate(items))
            return Reply(f"{lead} {body}", lang, "news", {"source": "rss", "count": len(items)})
        return Reply(
            msg("news.unavailable", lang),
            lang, "error", {"reason": "news_unavailable"},
        )

    def _radio(self, text: str, lang: str) -> Reply:
        """Play a named station, or offer the headline stations if none is named."""
        if not self.radio.available:
            return Reply(
                msg("radio.not_configured", lang),
                lang, "radio_offer", {},
            )
        station = self.radio.resolve(text)
        if station is None:
            names = [s.name for s in self.radio.offer()]
            if len(names) > 1:
                joined = ", ".join(names[:-1]) + msg("radio.offer_last", lang, station=names[-1])
            else:
                joined = names[0]
            return Reply(
                msg("radio.offer", lang, stations=joined),
                lang, "radio_offer", {"stations": [s.id for s in self.radio.offer()]},
            )
        # "Włączam stację <name>" (apposition) keeps the name in the nominative
        # so single-word channel nicknames don't need accusative inflection
        # ("Włączam Trójka" → grammatically "Trójkę"); works for every station.
        return Reply(
            msg("radio.station_on", lang, station=station.name),
            lang, "radio_play", {"id": station.id, "name": station.name, "url": station.url},
        )

    def _radio_stop(self, lang: str) -> Reply:
        return Reply(msg("radio.off", lang), lang, "radio_stop", {})

    def _lookup(self, query: str, lang: str) -> Reply:
        """Open/summarise a site or answer a general up-to-date query via Gemini."""
        if not self.gemini.available:
            return Reply(
                msg("web.needs_key", lang),
                lang, "news", {"needs_key": True},
            )
        sys = msg("prompts.web_lookup.system", lang, persona=self.persona)
        try:
            answer = self.gemini.grounded(query, system=sys)
        except GeminiError as e:
            log.warning("site lookup via Gemini failed (%s)", e)
            return Reply(
                msg("web.lookup_failed", lang),
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
        """One chat turn WITH conversation memory: delegates to `_chat_once`
        and, on a real answer, records the exchange so the next turn carries
        the thread (conversation mode, 2026-08-30)."""
        reply = self._chat_once(text, lang, task=task,
                                on_sentence=on_sentence, on_token=on_token)
        if reply.action == "chat" and reply.text and not reply.data.get("needs_key"):
            self._remember_turn(text, reply.text)
        return reply

    def _chat_once(
        self,
        text: str,
        lang: str,
        *,
        task: Task = Task.COMMAND,
        on_sentence: OnSentence | None = None,
        on_token: OnToken | None = None,
    ) -> Reply:
        # Prior turns for the LLM. Passed only when non-empty so single-turn
        # callers (and test fakes without the kwarg) stay byte-identical.
        hist = {"history": list(self._chat_history)} if self._chat_history else {}
        # Personalise with the user's name when known.
        system = self.persona
        name = self.memory.get_profile("name")
        if name:
            system += msg("prompts.chat.user_name", lang, name=name)
        # Retrieve the user's relevant stored notes and inject them. Shared
        # `system=` kwarg → this covers every backend uniformly. PRIVACY GATE:
        # the memory block reaches CLOUD engines only when embeddings.yaml
        # `notes_context.share_with_cloud` allows it (default: it does not);
        # on-device and LAN-mesh backends always get it.
        system_local = system + self._notes_context(text, lang)
        system_cloud = system_local if self._notes_share_with_cloud else system
        # Router path (production): try each available backend for this task in the
        # configured order — COMMAND→1.5B, RECOMMEND→4.5B, OPEN_QA→gpt-5.5, all
        # preferring the 11B-Ollama when reachable. Falls through to Gemini/canned.
        if self.router is not None:
            reply = self._route_chat(text, system_local, system_cloud, lang, task,
                                     on_sentence, on_token, hist=hist)
            if reply is not None:
                return reply
        else:
            # Legacy single-backend chain (unit tests inject llm/openai directly,
            # no router). Local-first — OpenAI is only a fallback here; the new
            # policy reserves the cloud for OPEN_QA, which the router handles.
            if self.llm is not None and self.llm.available:
                if on_sentence is not None:
                    return self._chat_stream_backend(
                        self.llm, "local", text, system_local, lang, on_sentence, on_token, hist=hist)
                try:
                    return Reply(self.llm.chat(text, system=system_local, **hist),
                                 lang, "chat", {"engine": "local"})
                except LlmError as e:
                    log.warning("local model failed (%s) — trying OpenAI/Gemini", e)
            if self.openai is not None and self.openai.available:
                try:
                    return Reply(self.openai.chat(text, system=system_cloud, **hist),
                                 lang, "chat", {"engine": "openai"})
                except OpenAiError as e:
                    log.warning("OpenAI fallback failed (%s)", e)
        if self.gemini.available:
            try:
                answer = self.gemini.chat(text, system=system_cloud, **hist)
            except GeminiError as e:
                return Reply(msg("error.chat_failed", lang, error=e), lang, "error")
            return Reply(answer, lang, "chat", {"engine": "gemini"})
        return Reply(
            msg("chat.needs_key", lang),
            lang, "chat", {"needs_key": True},
        )

    def _route_chat(
        self,
        text: str,
        system: str,
        system_cloud: str,
        lang: str,
        task: Task,
        on_sentence: OnSentence | None,
        on_token: OnToken | None,
        hist: dict[str, list[tuple[str, str]]] | None = None,
    ) -> Reply | None:
        """Try each available backend for ``task`` in order; return the first
        success, or ``None`` so ``_chat`` falls through to Gemini/canned. A
        backend that fails BEFORE emitting anything is skipped for the next.
        Cloud engines get ``system_cloud`` (the memory block withheld unless
        `share_with_cloud`); everything on-device/LAN gets ``system``."""
        for name, backend in self.router.route(task):  # type: ignore[union-attr]
            sys_for = system_cloud if name in _CLOUD_ENGINES else system
            try:
                if on_sentence is not None:
                    return self._chat_stream_backend(
                        backend, name, text, sys_for, lang, on_sentence, on_token,
                        task=task, hist=hist)
                answer = backend.chat(text, system=sys_for, **(hist or {}))
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
        hist: dict[str, list[tuple[str, str]]] | None = None,
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
            for chunk in backend.chat_stream(text, system=system, **(hist or {})):
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
        """Fire any due reminders as spoken replies (call on a timer tick).

        Verbose-state phrasing (user 2026-08-21): an alarm is a wake-up call —
        say the time in Polish words and repeat the call — not a bare label."""
        from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.plnum import time_words

        out: list[Reply] = []
        for r in self.memory.due(now):
            cat = getattr(r, "category", "reminder")
            task = r.text.strip()
            # Announcements are spoken in Polish (runtime language pinned "pl"
            # below); the catalog carries EN twins for asset parity.
            if cat == "alarm":
                due_dt = datetime.fromisoformat(r.due)
                extra = ("" if task in ("", "budzik", "alarm")
                         else msg("reminder.fired_task", "pl", task=task))
                text = msg("reminder.fired_alarm", "pl",
                           time=time_words(due_dt.hour, due_dt.minute), extra=extra)
            elif cat == "event":
                text = msg("reminder.fired_event", "pl", task=task)
            else:
                text = msg("reminder.fired", "pl", task=task)
            out.append(
                Reply(text, "pl", "reminder",
                      {"id": r.id, "fired": True, "category": cat}),
            )
        return out
