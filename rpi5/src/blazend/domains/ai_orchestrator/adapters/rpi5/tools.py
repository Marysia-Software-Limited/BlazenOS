"""Tool services — the API/ML glue the Rust dispatch calls (Phase 4d).

Each tool takes **structured args** (the NLU already extracted the slots) and
returns a [`ToolResult`] the mind speaks. Ported faithfully from the old
engine's tool methods (`_weather`, `_news`, `_recall_notes`, `_radio`, …) so
behaviour and phrasing are byte-identical; the engine is retired at the Phase 4
cutover and this becomes the single home of the tool logic.

Stays Python because every tool here is API/ML/hardware glue (Open-Meteo,
Gemini, RSS, the station directory, the memory store) — see
`docs/14-RUST-PYTHON-SPLIT.md` §1. Reached over the `tool.request` /
`tool.response` IPC seam by `tool_server`.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.audiobook_progress import (
    AudiobookProgress,
)
from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.audiobooks import (
    AudiobookDirectory,
    Book,
)
from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.gemini import (
    GeminiClient,
    GeminiError,
)
from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.music import MusicDirectory
from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.news import NewsClient
from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.openai import (
    OpenAiClient,
    OpenAiError,
)
from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.radio import RadioDirectory
from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.semantic import SemanticLibrary
from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.weather import (
    WeatherClient,
    WeatherError,
    describe_code,
)
from blazend.domains.context.adapters.rpi5.embeddings import EmbedderError
from blazend.domains.context.adapters.rpi5.memory import MemoryStore
from blazend.domains.context.adapters.rpi5.timeparse import parse_when

log = logging.getLogger("blazend.domains.ai_orchestrator.tools")

# The Jessica persona (same text as engine.PERSONA / jessica_core::DEFAULT_PERSONA).
# Lives here now that the engine is being retired; used for the grounded-LLM
# system prompts (news, web lookup).
PERSONA = (
    "Jesteś Jessica — głosowa asystentka osobista dla osób niewidomych i "
    "słabowidzących, działająca na Raspberry Pi 5. Odpowiadaj w języku "
    "użytkownika (polski lub angielski); domyślnie po polsku. Mów krótko — "
    "jedno lub dwa zdania, chyba że poproszono o szczegóły. Bądź konkretna i "
    "uczciwa; jeśli czegoś nie wiesz, powiedz to wprost."
)


def _t(lang: str, pl: str, en: str) -> str:
    return pl if lang == "pl" else en


def _pl_tracks(n: int) -> str:
    """Polish plural for track counts: 1 utwór, 2-4 utwory, 5+ utworów."""
    if n == 1:
        return "1 utwór"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return f"{n} utwory"
    return f"{n} utworów"


def _pl_memos(n: int) -> str:
    """Polish plural for voice notes: 1 nagranie, 2-4 nagrania, 5+ nagrań."""
    if n == 1:
        return "1 nagranie"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return f"{n} nagrania"
    return f"{n} nagrań"


def _queue_labels(tracks: Sequence[Any]) -> list[str]:
    """Spoken per-track names for a queue payload: the index's (repaired) title,
    prefixed with the artist when the queue mixes artists ("zagraj wszystko").
    An empty label ⇒ the orchestrator falls back to the filename stem."""
    multi = len({t.artist for t in tracks if t.artist}) > 1
    return [(f"{t.artist} — {t.title}" if multi and t.artist and t.title
             else t.title).strip() for t in tracks]


def _memo_label(title: str, text: str) -> str:
    """Spoken name of a memo in a queue: its title, else the first words of the
    transcript — never the wav filename ("vn-3" says nothing to a blind user)."""
    label = title.strip()
    if label:
        return label
    return " ".join(text.split()[:6])


def _pl_notes_count(n: int) -> str:
    """Polish plural for notes after "mam" (accusative): 1 notatkę,
    2-4 notatki, 5+ notatek."""
    if n == 1:
        return "1 notatkę"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return f"{n} notatki"
    return f"{n} notatek"


# Reminder parsing helpers (ported from the engine). The NLU strips the trigger
# verb; the tool removes the time expression + leftover filler to get the task.
_TIME_SPAN = re.compile(
    r"\b((za|in)\s+(\d+\s+)?\w+|((o|at)\s+\d{1,2}(:\d{2})?\s*(am|pm)?))\b"
    r"|\b(jutro|tomorrow)\b",
    re.IGNORECASE,
)
_ALARM_HINT = re.compile(r"\b(alarm|budzik|wake\s+me|timer)\b", re.IGNORECASE)
_EVENT_HINT = re.compile(r"\b(wydarzenie|spotkanie|event|meeting)\b", re.IGNORECASE)
_LEAD_FILLER = re.compile(
    r"^(?:mi|mnie|me|że|ze|żeby|zeby|aby|abym|o\s+tym,?\s*że|to|that|about|"
    r"hej|hey|cześć|czesc|ok|okej|okay)\b[\s,]*",
    re.IGNORECASE,
)


def _tidy(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip(" ,.:;!?–—-")


# Strip web-search citations/links + Markdown so the spoken text is clean: no URLs,
# no [1], and no `**bold**`/`#` headers a TTS would read as "gwiazdka" / "hash".
_MD_LINK = re.compile(r"\[([^\]]+)\]\((?:https?://[^)]+)\)")
_URL = re.compile(r"\(https?://[^)]+\)|https?://\S+")
_CITE = re.compile(r"\[\d+\]")
# Web-research answers cite bare domains — "(cracovia.pl)", "(ekstraklasa.org)" —
# which TTS would read aloud; drop any parenthesized domain-looking token.
_DOMAIN_CITE = re.compile(r"\s*\((?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^)\s]*)?\)", re.IGNORECASE)
_MD_EMPH = re.compile(r"[*_`]{1,3}")            # ** bold, * italic, ` code
_MD_HEAD = re.compile(r"(?m)^\s{0,3}#{1,6}\s*")  # # headers at line start


def _clean_spoken(text: str) -> str:
    text = _MD_LINK.sub(r"\1", text)
    text = _URL.sub("", text)
    text = _CITE.sub("", text)
    text = _DOMAIN_CITE.sub("", text)
    text = _MD_HEAD.sub("", text)
    text = _MD_EMPH.sub("", text)
    return re.sub(r"\s+", " ", text).strip()     # collapse newlines too (one spoken line)


def _strip_lead(text: str) -> str:
    text = _tidy(text)
    prev = None
    while prev != text:
        prev = text
        text = _tidy(_LEAD_FILLER.sub("", text))
    return text


@dataclass
class ToolResult:
    """A tool's outcome: spoken ``text`` plus an optional side effect."""

    ok: bool
    text: str
    action: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


class Tools:
    """The assistant's tools, behind structured-arg methods.

    Clients are built once and reused. Each is independently constructible so
    tests inject fakes; production uses the on-device defaults.
    """

    def __init__(
        self,
        *,
        memory: MemoryStore | None = None,
        weather: WeatherClient | None = None,
        gemini: GeminiClient | None = None,
        news: NewsClient | None = None,
        radio: RadioDirectory | None = None,
        music: MusicDirectory | None = None,
        openai: OpenAiClient | None = None,
        persona: str = PERSONA,
    ) -> None:
        self.memory = memory or MemoryStore()
        self.weather = weather or WeatherClient()
        self.gemini = gemini or GeminiClient()
        self.news = news or NewsClient()
        # OpenAI client for live news (web-search model). Built lazily from news.yaml
        # so the key (OPENAI_API_KEY / CHAT_GPT_KEY) is read at call time; injectable.
        self._openai = openai
        self.radio = radio or RadioDirectory()
        self.music = music or MusicDirectory()
        self.audiobooks = AudiobookDirectory()
        self.semantic = SemanticLibrary()  # voice semantic search over music + books
        self.persona = persona
        # Last music request ("coś Kazika" / "Kazika"), so "zagraj inny" replays it
        # → resolve() picks a fresh random track from the same pool (same artist).
        self._last_query = ""
        # Play history for "następny" / "poprzedni": a list of {path,name} with a
        # cursor. next steps forward (or plays a fresh track at the live edge);
        # previous steps back. So navigation is deterministic, not just re-random.
        self._history: list[dict[str, str]] = []
        self._cursor = -1

    # -- dispatch ----------------------------------------------------------
    def run(self, tool: str, args: dict[str, Any], lang: str) -> ToolResult:
        """Route a ``tool`` name + ``args`` to its handler."""
        if tool == "context.recall":
            return self.recall_notes(lang)
        if tool == "context.search_memory":
            return self.search_memory(str(args.get("query", "")), lang)
        if tool == "context.play_memos":
            return self.play_memos(lang)
        if tool == "context.play_found":
            return self.play_found_memo(lang)
        if tool == "context.memory_stats":
            return self.memory_stats(lang)
        if tool == "context.delete_last":
            return self.delete_last_memory(lang)
        if tool == "context.recall_reminders":
            return self.recall_reminders(lang)
        if tool == "context.remember":
            return self.remember(str(args.get("text", "")), lang)
        if tool == "context.set_name":
            return self.set_name(str(args.get("name", "")), lang)
        if tool == "context.add_reminder":
            return self.add_reminder(str(args.get("text", "")), lang)
        if tool == "weather.query":
            return self.weather_now(args.get("place"), lang)
        if tool == "rain.forecast":
            return self.rain_forecast(args.get("place"), str(args.get("when", "")), lang)
        if tool == "news.brief":
            return self.news_brief(lang)
        if tool == "news.sport":
            return self.news_sport(lang)
        if tool == "help.commands":
            return self.help_commands(lang)
        if tool == "web.lookup":
            return self.web_lookup(str(args.get("query", "")), lang)
        if tool == "radio.play":
            return self.radio_play(str(args.get("query", "")), lang)
        if tool == "radio.stop":
            return self.radio_stop(lang)
        if tool == "music.play":
            return self.music_play(str(args.get("query", "")), lang)
        if tool == "music.next":
            return self.music_next(lang)
        if tool == "music.prev":
            return self.music_prev(lang)
        if tool == "audiobook.play":
            return self.audiobook_play(str(args.get("query", "")), lang)
        if tool == "audiobooks.list":
            return self.audiobook_list(lang)
        if tool == "library.search":
            return self.library_search(str(args.get("query", "")), lang)
        return ToolResult(False, _t(lang, "Nie znam tego narzędzia.", "I don't know that tool."), "error")

    # -- context -----------------------------------------------------------
    def recall_notes(self, lang: str) -> ToolResult:
        # One memory, whatever its form: text notes + transcribed voice memos.
        items = self.memory.memory_items()
        if not items:
            return ToolResult(True, _t(lang, "Nic jeszcze nie zapisałam.", "I haven't noted anything yet."), "recall")
        lines = "; ".join(i.text for i in items[-10:])
        return ToolResult(True, _t(lang, f"Pamiętam: {lines}.", f"I remember: {lines}."), "recall", {"count": len(items)})

    def _memory_embedder(self) -> Any:
        """Lazy on-device e5 embedder for explicit memory search (same wiring
        as the brain's recall; the ONNX model loads on first embed only)."""
        if not hasattr(self, "_embedder_cache"):
            from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.context_wiring import (
                notes_context_wiring,
            )
            self._wiring = notes_context_wiring()
            self._embedder_cache = self._wiring.embedder
        return self._embedder_cache

    def search_memory(self, query: str, lang: str) -> ToolResult:
        """"Co zapisałem o X?" — semantic search over the unified memory
        (notes + voice-memo transcripts), lexical fallback when the embedder
        is absent. A voice-memo hit stashes its wav so "odtwórz nagranie"
        replays the user's own recording."""
        q = query.strip(" ,.?!")
        if not q:
            return ToolResult(True, _t(lang, "O czym mam poszukać?", "Search for what?"), "recall")
        hits = []
        emb = self._memory_embedder()
        if emb is not None and emb.available:
            try:
                qvec = emb.embed([q], kind="query")[0]
                hits = self.memory.search_memory_semantic(
                    qvec, limit=3,
                    min_score=self._wiring.min_score,
                    rel_margin=self._wiring.rel_margin,
                )
            except EmbedderError:
                hits = []
        if not hits:  # CPU-contract fallback: plain substring recall
            folded = q.casefold()
            hits = [i for i in self.memory.memory_items()
                    if folded in i.text.casefold() or folded in i.title.casefold()][:3]
        if not hits:
            return ToolResult(
                True, _t(lang, f"Nie znalazłam nic o: {q}.", f"I found nothing about {q}."),
                "recall", {"query": q, "hits": 0})
        lines = "; ".join(h.text for h in hits)
        spoken = _t(lang, f"Znalazłam: {lines}.", f"I found: {lines}.")
        resolved = ((self.memory.voice_note_wav(h.id, h.audio_path), _memo_label(h.title, h.text))
                    for h in hits if h.kind == "voice")
        found = [(str(w), label) for w, label in resolved if w is not None]
        voice_paths = [p for p, _ in found]
        self._found_memo_paths = voice_paths
        self._found_memo_labels = dict(found)
        if voice_paths:
            spoken += _t(lang, " Mam też nagranie — powiedz „odtwórz nagranie”.",
                         " I also have the recording — say “play the recording”.")
        return ToolResult(True, spoken, "recall",
                          {"query": q, "hits": len(hits), "voice_hits": len(voice_paths)})

    def _memo_queue(self, paths: list[str], lang: str,
                    labels: list[str] | None = None) -> ToolResult:
        n = len(paths)
        return ToolResult(
            True,
            _t(lang, f"Odtwarzam {_pl_memos(n)}.", f"Playing {n} voice note{'s' if n != 1 else ''}."),
            "music_play", {
                "path": paths[0], "name": _t(lang, "notatki głosowe", "voice notes"),
                "is_playlist": True, "chapters": paths, "chapter": 0,
                "labels": list(labels or []),
            })

    def play_memos(self, lang: str) -> ToolResult:
        """"Odtwórz notatki" — the newest voice memos as a playlist queue (the
        album engine drives playback: auto-advance, next/prev, stop). Resolves
        through `voice_note_wav`, so memos recorded on ANOTHER node play from
        their fabric-synced mirror."""
        pairs = [(str(w), _memo_label(v.title, v.transcript))
                 for v, w in ((v, self.memory.voice_note_wav(v.id, v.audio_path))
                              for v in self.memory.voice_notes())
                 if w is not None][-5:]
        if not pairs:
            return ToolResult(True, _t(lang, "Nie mam żadnych nagrań.", "I have no recordings."), "recall")
        return self._memo_queue([p for p, _ in pairs], lang,
                                labels=[label for _, label in pairs])

    def play_found_memo(self, lang: str) -> ToolResult:
        """"Odtwórz (to) nagranie" — replay what the last memory search found
        (falling back to the newest memos when nothing was searched)."""
        paths = [p for p in getattr(self, "_found_memo_paths", []) if Path(p).exists()]
        if not paths:
            return self.play_memos(lang)
        found = getattr(self, "_found_memo_labels", {})
        return self._memo_queue(paths, lang, labels=[found.get(p, "") for p in paths])

    def memory_stats(self, lang: str) -> ToolResult:
        """"Ile mam notatek?" — spoken inventory of the memory store."""
        notes = self.memory.notes()
        memos = self.memory.voice_notes()
        if not notes and not memos:
            return ToolResult(
                True, _t(lang, "Nie mam jeszcze żadnych wspomnień.", "I have no memories yet."),
                "recall", {"notes": 0, "voice_notes": 0})
        if lang == "pl":
            parts = []
            if notes:
                parts.append(_pl_notes_count(len(notes)))
            if memos:
                parts.append(_pl_memos(len(memos)))
            spoken = f"Mam {' i '.join(parts)}."
        else:
            spoken = (f"I have {len(notes)} note{'s' if len(notes) != 1 else ''}"
                      f" and {len(memos)} recording{'s' if len(memos) != 1 else ''}.")
        return ToolResult(True, spoken, "recall",
                          {"notes": len(notes), "voice_notes": len(memos)})

    def delete_last_memory(self, lang: str) -> ToolResult:
        """"Usuń ostatnią notatkę" — remove the newest memory. Safe without a
        confirmation dance: only ONE item goes, the confirmation reads back
        exactly what was removed, and a memo's wav lands in <data>/trash/
        (recoverable by hand), never straight to deletion."""
        item = self.memory.delete_last_memory()
        if item is None:
            return ToolResult(True, _t(lang, "Nie mam czego usunąć.", "There is nothing to delete."),
                              "recall", {"deleted": None})
        label = item.text[:80] or _t(lang, "nagranie bez słów", "a recording with no words")
        return ToolResult(
            True, _t(lang, f"Usunęłam: {label}.", f"Deleted: {label}."),
            "recall", {"deleted": item.id, "kind": item.kind})

    def recall_reminders(self, lang: str) -> ToolResult:
        pend = self.memory.pending()
        if not pend:
            return ToolResult(True, _t(lang, "Nie masz żadnych przypomnień.", "You have no reminders."), "recall")
        lines = "; ".join(
            f"{datetime.fromisoformat(r.due).strftime('%H:%M')} — {r.text}" for r in pend[:10]
        )
        return ToolResult(
            True, _t(lang, f"Twoje przypomnienia: {lines}.", f"Your reminders: {lines}."), "recall", {"count": len(pend)}
        )

    # -- memory writes -----------------------------------------------------
    def remember(self, text: str, lang: str) -> ToolResult:
        """Store a memory. The NLU already stripped the trigger verb, so `text`
        is the body. When the ASR's clip of this very utterance is claimable,
        the memory keeps its own RECORDING too (VoiceNote: sound + text —
        decision 2026-07-27); otherwise it is a plain text note. Embedding is
        handled lazily by the brain's semantic-recall backfill."""
        body = text.strip(" ,.:!")
        if not body:
            return ToolResult(True, _t(lang, "Co mam zapamiętać?", "What should I remember?"), "wake")
        spoken_ok = _t(lang, f"Zapamiętałam: {body}.", f"Got it, I'll remember: {body}.")
        claimed = self.memory.claim_last_clip(body)
        if claimed is not None:
            vn = self.memory.add_voice_note(
                claimed[0], now=datetime.now(), duration_s=claimed[1], transcript=body)
            return ToolResult(True, spoken_ok, "note",
                              {"id": vn.id, "text": body, "audio_path": vn.audio_path})
        note = self.memory.add_note(body, now=datetime.now())
        return ToolResult(True, spoken_ok, "note", {"id": note.id, "text": note.text})

    def set_name(self, name: str, lang: str) -> ToolResult:
        """Store the user's name (capitalised), mirroring the engine's `_set_name`."""
        name = name.strip()
        if not name:
            return ToolResult(True, _t(lang, "Jak mam Cię nazywać?", "What should I call you?"), "wake")
        name = name[:1].upper() + name[1:]
        self.memory.set_profile("name", name, now=datetime.now())
        return ToolResult(
            True,
            _t(lang, f"Miło mi, {name}! Zapamiętam.", f"Nice to meet you, {name}! I'll remember."),
            "profile", {"name": name},
        )

    def add_reminder(self, text: str, lang: str) -> ToolResult:
        """Parse a spoken reminder ("o spotkaniu o 15:00") and store it.

        Mirrors the engine's `_remind`: `parse_when` for the time, hint regexes
        for the category, then the time + filler stripped to leave the task.
        """
        now = datetime.now()
        when = parse_when(text, now)
        category = (
            "alarm" if _ALARM_HINT.search(text)
            else "event" if _EVENT_HINT.search(text)
            else "reminder"
        )
        task = _strip_lead(_TIME_SPAN.sub("", text))
        if when is None:
            return ToolResult(
                True,
                _t(lang, "Na kiedy? Np. „o 15:00” albo „za 10 minut”.",
                   'When? E.g. "at 3pm" or "in 10 minutes".'),
                "wake",
            )
        if not task:
            task = {"alarm": _t(lang, "budzik", "alarm"), "event": _t(lang, "wydarzenie", "event")}.get(
                category, _t(lang, "przypomnienie", "reminder")
            )
        rem = self.memory.add_reminder(task, due=when, now=now, category=category)
        hhmm = when.strftime("%H:%M")
        lead = {
            "alarm": _t(lang, f"Ustawiłam budzik na {hhmm}", f"Alarm set for {hhmm}"),
            "event": _t(lang, f"Zapisałam na {hhmm}", f"Saved for {hhmm}"),
        }.get(category, _t(lang, f"Przypomnę Ci o {hhmm}", f"I'll remind you at {hhmm}"))
        return ToolResult(True, f"{lead}: {task}.", "reminder", {"id": rem.id, "due": rem.due, "category": category})

    # -- weather -----------------------------------------------------------
    def weather_now(self, place_name: str | None, lang: str) -> ToolResult:
        place = None
        if place_name and place_name.strip():
            try:
                place = self.weather.geocode(place_name.strip(), lang)
            except WeatherError:
                place = None
        try:
            c = self.weather.current(place)
        except WeatherError as e:
            log.warning("weather lookup failed (%s)", e)
            return ToolResult(
                False, _t(lang, "Nie mogę teraz sprawdzić pogody.", "I can't check the weather right now."),
                "error", {"reason": "weather_failed"},
            )
        desc = describe_code(c.code, lang)
        temp = round(c.temperature)
        rng_txt = ""
        if c.temp_max is not None and c.temp_min is not None:
            lo, hi = round(c.temp_min), round(c.temp_max)
            rng_txt = (f", od {lo} do {hi}{c.units_temp}" if lang == "pl"
                       else f", {lo} to {hi}{c.units_temp}")
        # A weather answer LEADS with — and focuses on — the chance of rain; temp,
        # sky and the day's range follow. Wind / feels-like are dropped so the rain
        # number isn't buried (they stay in Conditions for callers that want them).
        if lang == "pl":
            rain = f"Szansa opadów {c.rain_prob}%. " if c.rain_prob is not None else ""
            out = f"{c.place}: {rain}Teraz {temp}{c.units_temp}, {desc}{rng_txt}."
        else:
            rain = f"Chance of rain {c.rain_prob}%. " if c.rain_prob is not None else ""
            out = f"{c.place}: {rain}Now {temp}{c.units_temp}, {desc}{rng_txt}."
        return ToolResult(True, out, "weather",
                          {"place": c.place, "temp": temp, "code": c.code, "rain_prob": c.rain_prob})

    # -- rain forecast (dedicated: rain probability + when) -----------------
    def rain_forecast(self, place_name: str | None, when: str, lang: str) -> ToolResult:
        """"czy będzie padać?" / "kiedy?" / "a jutro?" — leads with the chance of
        rain and, when it's likely, the hour it peaks. On missing rain data (or a
        lookup failure) Jessica says she has no access to the rain forecast."""
        place = None
        if place_name and place_name.strip():
            try:
                place = self.weather.geocode(place_name.strip(), lang)
            except WeatherError:
                place = None
        try:
            o = self.weather.rain(place)
        except WeatherError as e:
            log.warning("rain lookup failed (%s)", e)
            return ToolResult(
                False,
                _t(lang, "Nie mam dostępu do prognozy opadów.",
                   "I don't have access to the rain forecast."),
                "error", {"reason": "rain_unavailable"},
            )
        if o.today_max is None and o.tomorrow_max is None:
            return ToolResult(
                False,
                _t(lang, "Nie mam dostępu do prognozy opadów.",
                   "I don't have access to the rain forecast."),
                "error", {"reason": "rain_unavailable"},
            )

        tomorrow = "tomorrow" in when.lower() or "jutro" in when.lower()
        if tomorrow and o.tomorrow_max is not None:
            text = _t(lang, f"Jutro szansa opadów {o.tomorrow_max}%.",
                      f"Tomorrow the chance of rain is {o.tomorrow_max}%.")
            return ToolResult(True, text, "rain",
                              {"when": "tomorrow", "place": o.place, "tomorrow": o.tomorrow_max})

        # Today (default): chance first, then WHEN it peaks, then tomorrow as a tail.
        today = o.today_max if o.today_max is not None else (o.peak_prob or 0)
        if lang == "pl":
            text = f"Szansa opadów dziś {today}%."
            if o.peak_hour is not None and o.peak_prob is not None and o.peak_prob >= self.weather._peak_threshold:
                text += f" Najwięcej koło {o.peak_hour}:00."
            if o.tomorrow_max is not None:
                text += f" Jutro {o.tomorrow_max}%."
        else:
            text = f"Chance of rain today {today}%."
            if o.peak_hour is not None and o.peak_prob is not None and o.peak_prob >= self.weather._peak_threshold:
                text += f" Highest around {o.peak_hour}:00."
            if o.tomorrow_max is not None:
                text += f" Tomorrow {o.tomorrow_max}%."
        return ToolResult(True, text, "rain",
                          {"when": "today", "place": o.place, "today": today,
                           "peak_hour": o.peak_hour, "tomorrow": o.tomorrow_max})

    # -- news --------------------------------------------------------------
    # The three tiers Jessica reads, in spoken order, with their section labels.
    _NEWS_TIERS = ("local", "national", "world")
    _NEWS_LABELS = {
        "pl": {"local": "Z Krakowa", "national": "Z kraju", "world": "Ze świata"},
        "en": {"local": "From Kraków", "national": "Nationally", "world": "Worldwide"},
    }

    def _news_composer(self) -> OpenAiClient:
        """OpenAI client that turns the collected headlines into a spoken Polish
        brief (translation + summarisation — a standard chat model, not search).
        Injectable via the ``openai`` ctor arg; key from OPENAI_API_KEY / CHAT_GPT_KEY."""
        if self._openai is None:
            self._openai = OpenAiClient()  # OPENAI_MODEL (default gpt-4o)
        return self._openai

    def _headline_block(self, tiers: dict[str, list[str]], lang: str) -> str:
        """The collected headlines as a labelled, grouped block for the composer."""
        labels = self._NEWS_LABELS.get(lang, self._NEWS_LABELS["pl"])
        lines: list[str] = []
        for key in self._NEWS_TIERS:
            items = tiers.get(key) or []
            if not items:
                continue
            lines.append(f"[{labels[key]}]")
            lines.extend(f"- {t}" for t in items)
        return "\n".join(lines)

    def _spoken_from_tiers(self, tiers: dict[str, list[str]], lang: str) -> ToolResult:
        """Keyless floor: read the tiers natively (Polish), two items each, no LLM."""
        labels = self._NEWS_LABELS.get(lang, self._NEWS_LABELS["pl"])
        parts: list[str] = []
        counts: dict[str, int] = {}
        for key in self._NEWS_TIERS:
            items = (tiers.get(key) or [])[:2]
            counts[key] = len(items)
            if items:
                parts.append(f"{labels[key]}: {'; '.join(items)}.")
        if not parts:
            return ToolResult(
                False,
                _t(lang, "Nie mogę teraz sprawdzić wiadomości.", "I can't check the news right now."),
                "error", {"reason": "news_unavailable"},
            )
        return ToolResult(True, " ".join(parts), "news", {"source": "rss", "tiers": counts})

    def _research_brief(self, query: str, sys: str, kind: str) -> tuple[bool, ToolResult | None]:
        """Preferred cloud path (user request 2026-08-21): GPT-5.6-sol searches
        the live internet via the hosted web_search tool and composes the brief
        itself. Returns (attempted, result): on failure the caller walks the
        ladder (RSS+compose → Gemini → keyless floor) and `attempted` lets the
        floor SAY the web search failed (verbose-state decision 2026-08-21)."""
        composer = self._news_composer()
        research = getattr(composer, "research", None)
        if not composer.available or research is None:
            return False, None
        try:
            answer = _clean_spoken(research(query, system=sys))
        except OpenAiError as e:
            log.warning("%s research via OpenAI failed (%s); trying RSS ladder", kind, e)
            return True, None
        if not answer:
            return True, None
        return True, ToolResult(True, answer, "news", {"source": "openai-web", "kind": kind})

    @staticmethod
    def _web_failed_preamble(lang: str) -> str:
        """Spoken state explanation for the RSS floor after a failed web search."""
        return _t(lang,
                  "Nie udało mi się przeszukać internetu, więc czytam nagłówki z serwisów. ",
                  "I couldn't search the web, so I'm reading feed headlines instead. ")

    def news_brief(self, lang: str) -> ToolResult:
        # Preferred: live internet research (web_search) — today's news straight
        # from the web, Kraków → kraj → świat.
        today = datetime.now().date().isoformat()
        if lang == "pl":
            rsys = (self.persona + " Jesteś prezenterką wiadomości. Zwykły tekst do "
                    "przeczytania na głos — bez Markdown, gwiazdek, nagłówków i URL-i.")
            rquery = (f"Znajdź w internecie najważniejsze DZISIEJSZE ({today}) wiadomości i "
                      "ułóż krótki mówiony serwis po polsku w trzech częściach, dokładnie w tej "
                      "kolejności: „Z Krakowa”, „Z kraju” (Polska), „Ze świata” — po dwie-trzy "
                      "wiadomości, każda jednym zdaniem. Tylko fakty znalezione w sieci.")
        else:
            rsys = (self.persona + " You are a news anchor. Plain text to be read aloud — "
                    "no Markdown, asterisks, headers or URLs.")
            rquery = (f"Search the internet for TODAY'S ({today}) top news and compose a short "
                      "spoken brief in three sections, in this exact order: From Kraków, From "
                      "Poland, Worldwide — two-three items each, one sentence each. Only facts "
                      "found on the web.")
        web_tried, hit = self._research_brief(rquery, rsys, "news")
        if hit is not None:
            return hit

        # Data is ALWAYS real RSS from the configured sources — keyless, on-device.
        # Kraków + kraj are Polish; the world tier is the international agencies
        # (English) the user asked for, translated into the Polish brief below.
        tiers = self.news.collect(self._NEWS_TIERS)

        # Preferred: the cloud composer folds the headlines into a spoken Polish
        # brief, translating the world agencies. Opt-in; any failure → the floor.
        composer = self._news_composer()
        block = self._headline_block(tiers, lang)
        if composer.available and block:
            if lang == "pl":
                sys = (self.persona + " Jesteś prezenterką wiadomości. Z podanych nagłówków ułóż "
                       "krótki, mówiony serwis po polsku w trzech częściach: „Z Krakowa”, „Z kraju”, "
                       "„Ze świata” — po dwie wiadomości w każdej, każda jednym zdaniem. Nagłówki "
                       "zagraniczne przetłumacz na polski. Nie dodawaj faktów spoza nagłówków, "
                       "bez wstępu, bez adresów URL. Zwykły tekst do przeczytania na głos — bez "
                       "formatowania Markdown, bez gwiazdek i nagłówków.")
                query = "Nagłówki na dziś:\n" + block
            else:
                sys = (self.persona + " You are a news anchor. From these headlines compose a short "
                       "spoken brief in three sections — From Kraków, Nationally, Worldwide — two "
                       "items each, one sentence each. Add no facts beyond the headlines, no "
                       "preamble, no URLs. Plain text to be read aloud — no Markdown, no asterisks "
                       "or headers.")
                query = "Today's headlines:\n" + block
            try:
                answer = _clean_spoken(composer.chat(query, system=sys))
                if answer:
                    return ToolResult(True, answer, "news",
                                      {"source": "openai", "tiers": {k: len(v) for k, v in tiers.items()}})
            except OpenAiError as e:
                log.warning("news compose via OpenAI failed (%s); trying Gemini/RSS", e)

        # Gemini does its own grounded Kraków+Poland search (independent of the RSS
        # block), kept as the secondary cloud path.
        if self.gemini.available:
            if lang == "pl":
                gquery = ("Podaj najważniejsze aktualne wiadomości z międzynarodowych agencji "
                          "(Guardian, BBC, CNN, AP), ze szczególnym uwzględnieniem Krakowa i Polski.")
                gsys = self.persona + (" Odpowiedz po polsku, zwięźle, 3-4 najważniejsze punkty. "
                                       "Przetłumacz nagłówki na polski.")
            else:
                gquery = ("Give the top current news from international agencies "
                          "(Guardian, BBC, CNN, AP), focusing on Kraków and Poland.")
                gsys = self.persona + " Answer in English, briefly — 3-4 key points."
            try:
                answer = self.gemini.grounded(gquery, system=gsys)
                return ToolResult(True, answer, "news", {"grounded": True, "focus": "krakow-poland"})
            except GeminiError as e:
                log.warning("news via Gemini failed (%s); falling back to RSS", e)

        # Keyless floor: local + national spoken natively; the world tier switches
        # to the Polish-language world feed so the brief stays fully Polish offline
        # (falling back to the English agencies only if no Polish world feed answers).
        floor = dict(tiers)
        floor["world"] = self.news.by_tier("world_pl") or tiers.get("world", [])
        res = self._spoken_from_tiers(floor, lang)
        if web_tried and res.ok:
            # Verbose state: the user asked for live news and got RSS — say why.
            return ToolResult(True, self._web_failed_preamble(lang) + res.text,
                              res.action, res.payload)
        return res

    def news_sport(self, lang: str) -> ToolResult:
        """Sport brief ("jak sport", user request 2026-08-21): football first,
        then the rest of sport — Kraków → Poland → world. Preferred path is live
        web research; the keyless floor reads the `sport` RSS tier."""
        today = datetime.now().date().isoformat()
        if lang == "pl":
            rsys = (self.persona + " Jesteś prezenterką sportową. Zwykły tekst do "
                    "przeczytania na głos — bez Markdown, gwiazdek, nagłówków i URL-i.")
            rquery = (f"Znajdź w internecie DZISIEJSZE ({today}) wiadomości sportowe — najpierw "
                      "piłka nożna, potem pozostałe dyscypliny — i ułóż krótki mówiony serwis po "
                      "polsku w trzech częściach, dokładnie w tej kolejności: „Z Krakowa” (Wisła "
                      "Kraków, Cracovia, krakowski sport), „Z kraju” (Ekstraklasa, reprezentacja "
                      "Polski, polscy sportowcy), „Ze świata” — po dwie-trzy wiadomości, każda "
                      "jednym zdaniem. Tylko fakty znalezione w sieci.")
        else:
            rsys = (self.persona + " You are a sports anchor. Plain text to be read aloud — "
                    "no Markdown, asterisks, headers or URLs.")
            rquery = (f"Search the internet for TODAY'S ({today}) sports news — football first, "
                      "then other sports — and compose a short spoken brief in three sections, in "
                      "this exact order: From Kraków (Wisła Kraków, Cracovia), From Poland "
                      "(Ekstraklasa, national team), Worldwide — two-three items each, one "
                      "sentence each. Only facts found on the web.")
        web_tried, hit = self._research_brief(rquery, rsys, "sport")
        if hit is not None:
            return hit

        # Keyless floor: the Polish `sport` RSS tier (nationwide — Kraków-specific
        # sport has no reliable feed; the research path covers it), read natively.
        items = (self.news.by_tier("sport") or [])[:4]
        if not items:
            return ToolResult(
                False,
                _t(lang, "Nie mogę teraz sprawdzić wiadomości sportowych.",
                   "I can't check the sports news right now."),
                "error", {"reason": "sport_unavailable"},
            )
        label = _t(lang, "Ze sportu", "In sports")
        preamble = self._web_failed_preamble(lang) if web_tried else ""
        return ToolResult(True, f"{preamble}{label}: {'; '.join(items)}.", "news",
                          {"source": "rss", "kind": "sport", "count": len(items)})

    # -- help ("jakie komendy") --------------------------------------------
    # Curated, spoken walkthrough of the command surface — grouped with example
    # phrases, because reading 80 regexes aloud is useless. Keep it in sync with
    # configs/intents/system.yaml when commands are added ("jak sport" etc.).
    _HELP_PL = (
        "Rozumiem między innymi. "
        "Sterowanie: stop, głośniej, ciszej, ustaw głośność na pięćdziesiąt, powtórz, "
        "idź spać, obudź się. "
        "Czas: która godzina, jaka jest data. "
        "Pogoda: jaka pogoda w Krakowie, czy będzie padać. "
        "Wiadomości: jakie wieści — serwis z Krakowa, z kraju i ze świata; "
        "jak sport — wiadomości sportowe, najpierw piłka nożna. "
        "Radio i muzyka: włącz radio Trójka, zagraj tytuł albo wykonawcę, następny, "
        "poprzedni, co teraz gra, wyłącz radio. "
        "Książki: jakie masz książki, włącz książkę, czytaj dalej, następny rozdział. "
        "Notatki i pamięć: zapamiętaj że…, nagraj notatkę głosową, odtwórz notatki, "
        "jakie mam notatki, co zapisałem o…, usuń ostatnią notatkę. "
        "Przypomnienia: przypomnij mi o…, jakie mam przypomnienia. "
        "Języki: mów po angielsku, mów po polsku. "
        "A gdy zapytasz o cokolwiek innego, po prostu odpowiem."
    )
    _HELP_EN = (
        "Among other things I understand. "
        "Control: stop, louder, quieter, set volume to fifty, repeat, go to sleep, wake up. "
        "Time: what time is it, what's the date. "
        "Weather: what's the weather in Kraków, will it rain. "
        "News: what's the news — a brief from Kraków, Poland and the world; "
        "sports news — football first. "
        "Radio and music: turn on radio, play a title or artist, next, previous, "
        "what's playing, turn the radio off. "
        "Books: what books do you have, play a book, keep reading, next chapter. "
        "Notes and memory: remember that…, record a voice memo, play my memos, "
        "what notes do I have, delete the last note. "
        "Reminders: remind me about…, what are my reminders. "
        "Languages: speak English, speak Polish. "
        "And for anything else, just ask."
    )

    def help_commands(self, lang: str) -> ToolResult:
        text = self._HELP_PL if lang == "pl" else self._HELP_EN
        return ToolResult(True, text, "help", {"kind": "commands"})

    # -- web lookup --------------------------------------------------------
    def web_lookup(self, query: str, lang: str) -> ToolResult:
        if not self.gemini.available:
            return ToolResult(
                False,
                _t(lang, "To wymaga konta Gemini — ustaw GEMINI_API_KEY.",
                   "That needs a Gemini account — set GEMINI_API_KEY."),
                "news", {"needs_key": True},
            )
        sys = self.persona + _t(lang, " Streść zwięźle, po polsku.", " Summarise briefly, in English.")
        try:
            answer = self.gemini.grounded(query, system=sys)
        except GeminiError as e:
            log.warning("site lookup via Gemini failed (%s)", e)
            return ToolResult(
                False, _t(lang, "Nie mogę teraz tego sprawdzić.", "I can't look that up right now."),
                "error", {"reason": "lookup_failed"},
            )
        return ToolResult(True, answer, "news", {"grounded": True})

    # -- radio -------------------------------------------------------------
    def radio_play(self, query: str, lang: str) -> ToolResult:
        if not self.radio.available:
            return ToolResult(True, _t(lang, "Radio nie jest skonfigurowane.", "Radio isn't configured."), "radio_offer")
        station = self.radio.resolve(query)
        if station is None:
            names = [s.name for s in self.radio.offer()]
            if len(names) > 1:
                joined = ", ".join(names[:-1]) + _t(lang, f" lub {names[-1]}", f" or {names[-1]}")
            else:
                joined = names[0] if names else ""
            return ToolResult(
                True,
                _t(lang, f"Mogę włączyć: {joined}. Którą stację?", f"I can play: {joined}. Which station?"),
                "radio_offer", {"stations": [s.id for s in self.radio.offer()]},
            )
        return ToolResult(
            True, _t(lang, f"Włączam stację {station.name}.", f"Playing {station.name}."),
            "radio_play", {"id": station.id, "name": station.name, "url": station.url},
        )

    def radio_stop(self, lang: str) -> ToolResult:
        return ToolResult(True, _t(lang, "Wyłączam radio.", "Turning off the radio."), "radio_stop")

    # -- music (offline local library) -------------------------------------
    def _play_track(self, path: str, name: str, lang: str) -> ToolResult:
        """Record a freshly-chosen track at the live edge of the history (dropping
        any forward branch) and return the play result."""
        self._history = self._history[: self._cursor + 1]
        self._history.append({"path": path, "name": name})
        self._cursor = len(self._history) - 1
        return ToolResult(True, _t(lang, f"Gram {name}.", f"Playing {name}."),
                          "music_play", {"path": path, "name": name})

    def _play_from_history(self, lang: str) -> ToolResult:
        item = self._history[self._cursor]
        return ToolResult(True, _t(lang, f"Gram {item['name']}.", f"Playing {item['name']}."),
                          "music_play", {"path": item["path"], "name": item["name"]})

    def music_play(self, query: str, lang: str) -> ToolResult:
        if not self.music.available:
            return ToolResult(True, _t(lang, "Biblioteka muzyki jest pusta.", "The music library is empty."), "music_offer")
        self._last_query = query  # remember context for "zagraj inny"
        # Queue ladder (decision 2026-07-27: album/artist requests play
        # EVERYTHING until "stop", not one surprise track): explicit
        # cały/wszystko/coś → shuffled; album name → in track order; artist
        # name → their whole catalogue shuffled; a title → that single track.
        # Spoken name = the user's words minus a leading container word, so
        # "zagraj album X" confirms as "Gram album X", not "Gram album album X".
        name = re.sub(r"^\s*(album\w*|p(?:ł|l)yt\w*)\s+", "", query.strip().rstrip(".?!"),
                      flags=re.IGNORECASE)
        queue = self.music.resolve_all(query)
        album = None if queue is not None else self.music.resolve_album(query)
        if album is not None:
            # Announce with the user's own words, not the ID3 album tag —
            # several rips carry mojibake tags that would garble the TTS.
            n = len(album)
            return ToolResult(
                True,
                _t(lang, f"Gram album {name} — {_pl_tracks(n)}.",
                   f"Playing the album {name} — {n} tracks."),
                "music_play", {
                    "path": album[0].path, "name": name, "is_playlist": True,
                    "chapters": [t.path for t in album], "chapter": 0,
                    # Spoken per-track names: the index's repaired titles, so
                    # now-playing/shuffle never read a mojibake filename aloud.
                    "labels": _queue_labels(album),
                })
        queue = queue or self.music.resolve_artist(query)
        if queue is not None:
            # The count is the confirmation; naming the pool would fight the
            # genitive the user just used, so keep it grammar-neutral.
            n = len(queue)
            return ToolResult(
                True,
                _t(lang, f"Dobrze — {_pl_tracks(n)} w losowej kolejności.",
                   f"Alright — {n} tracks, shuffled."),
                "music_play", {
                    "path": queue[0].path, "name": name, "is_playlist": True,
                    "chapters": [t.path for t in queue], "chapter": 0,
                    "labels": _queue_labels(queue),
                })
        track = self.music.resolve(query)
        if track is None:
            # No literal match → fall back to MEANING (e.g. "coś spokojnego").
            return self.library_search(query, lang, kinds=("music",))
        who = f"{track.artist} — {track.title}" if track.artist else track.title
        return self._play_track(track.path, who, lang)

    def music_next(self, lang: str) -> ToolResult:
        """"Następny" / "zagraj inny" — step FORWARD in history if we'd gone back,
        otherwise play a fresh track from the last request (bare artist pool → a new
        random track of that artist)."""
        if self._cursor < len(self._history) - 1:
            self._cursor += 1
            return self._play_from_history(lang)
        return self.music_play(self._last_query, lang)

    def music_prev(self, lang: str) -> ToolResult:
        """"Poprzedni" — step BACK to the previously played track."""
        if self._cursor > 0:
            self._cursor -= 1
            return self._play_from_history(lang)
        return ToolResult(True, _t(lang, "To już pierwszy utwór.", "This is the first track."), "music_offer")

    # -- audiobooks (offline, from catalog.json) ---------------------------
    def _book_result(self, book: Book, lang: str) -> ToolResult:
        """Play a book — RESUME at the saved chapter+offset if we've heard it
        before, else start from chapter 0. The payload carries the whole book so
        the orchestrator can auto-advance chapters, remember position, and resume."""
        who = f"{book.author} — {book.title}" if book.author else book.title
        prog = AudiobookProgress().get(book.slug) or {}  # fresh read; supervisor is the writer
        chapter = int(prog.get("chapter", 0))
        offset = float(prog.get("offset_s", 0.0))
        resumed = bool(prog) and (chapter > 0 or offset > 5.0)
        if chapter >= len(book.chapters):
            chapter, offset, resumed = 0, 0.0, False
        lead = (_t(lang, f"Wznawiam: {who}.", f"Resuming: {who}.") if resumed
                else _t(lang, f"Czytam: {who}.", f"Reading: {who}."))
        return ToolResult(True, lead, "music_play", {
            "path": book.chapters[chapter], "name": who,
            "is_audiobook": True, "slug": book.slug,
            "chapters": list(book.chapters), "chapter": chapter,
            "start_seconds": offset,
        })

    def audiobook_play(self, query: str, lang: str) -> ToolResult:
        if not self.audiobooks.available:
            return ToolResult(True, _t(lang, "Nie mam jeszcze audiobooków.", "I have no audiobooks yet."), "audiobook_offer")
        book = self.audiobooks.resolve(query)
        if book is None:
            # No literal match → try MEANING; map the top book hit back to a Book
            # (by title) so it still gets the full resume/chapter engine.
            for item in self.semantic.search(query, k=1, kinds=("book",)):
                b = self.audiobooks.resolve(item.get("title", ""))
                if b is not None:
                    return self._book_result(b, lang)
            titles = ", ".join(b.title for b in self.audiobooks.offer())
            return ToolResult(
                True,
                _t(lang, f"Nie znalazłam „{query}”. Mam: {titles}.", f"I couldn't find “{query}”. I have: {titles}."),
                "audiobook_offer",
            )
        return self._book_result(book, lang)

    def audiobook_list(self, lang: str) -> ToolResult:
        """Read back a spoken menu of the available audiobooks (from the shared
        catalog), so a screenless user can hear what there is to read."""
        if not self.audiobooks.available:
            return ToolResult(
                True,
                _t(lang, "Nie mam jeszcze żadnych audiobooków.", "I don't have any audiobooks yet."),
                "audiobook_offer",
            )
        total = len(self.audiobooks.books)
        titles = ", ".join(b.title for b in self.audiobooks.offer(limit=3))
        if lang == "pl":
            out = f"Mam {total} audiobooków, na przykład: {titles}. Powiedz „przeczytaj” i tytuł."
        else:
            out = f"I have {total} audiobooks, for example: {titles}. Say “read” and a title."
        return ToolResult(True, out, "audiobook_offer", {"count": total})

    # -- semantic search (music + books BY MEANING) ------------------------
    def _play_item(self, item: dict[str, Any], lang: str) -> ToolResult | None:
        who = " — ".join(x for x in (item.get("who", ""), item.get("title", "")) if x)
        if item.get("type") == "book":
            ch = item.get("chapters") or []
            return ToolResult(True, _t(lang, f"Czytam: {who}.", f"Reading: {who}."),
                              "music_play", {"path": ch[0], "name": who}) if ch else None
        path = str(item.get("path", ""))
        return ToolResult(True, _t(lang, f"Gram {who}.", f"Playing {who}."),
                          "music_play", {"path": path, "name": who}) if path else None

    def library_search(self, query: str, lang: str, *, kinds: tuple[str, ...] | None = None) -> ToolResult:
        """Semantic (meaning-based) search over the music + audiobook index → play
        the best hit. Backs 'znajdź coś spokojnego' and the resolve fallbacks."""
        for item in self.semantic.search(query, k=1, kinds=kinds):
            played = self._play_item(item, lang)
            if played is not None:
                return played
        return ToolResult(True, _t(lang, f"Nie znalazłam nic pasującego do „{query}”.",
                                   f"I found nothing matching “{query}”."), "music_offer")
