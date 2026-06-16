"""The assistant engine — Jessica's conversation logic.

Ties the pieces together: name/wake gating, the remember / remind / recall
commands (bilingual), news + site lookups via Gemini, and freeform Polish
conversation via Gemini. Pure and synchronous so it is trivially testable;
the REPL and the IPC `blazend-brain` unit both drive it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from blazend.assistant import wake
from blazend.assistant.gemini import GeminiClient, GeminiError
from blazend.assistant.localllm import LlmError, LocalLlm
from blazend.assistant.memory import MemoryStore
from blazend.assistant.openai import OpenAiClient, OpenAiError
from blazend.assistant.timeparse import parse_when

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
_SITE = re.compile(
    r"\b(stron[aęy]|otwórz|otworz|sprawdź\s+na|sprawdz\s+na|site|open|summari[sz]e|streść|stresc)\b|https?://",
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


@dataclass
class Reply:
    """The assistant's response to one utterance."""

    text: str
    language: str = "pl"
    # wake | asleep | note | reminder | recall | news | chat | profile | error
    #   | voice_note_record | voice_note_play
    action: str = "chat"
    data: dict[str, Any] = field(default_factory=dict)


class Assistant:
    """Stateful conversation engine. ``route()`` handles one utterance."""

    def __init__(
        self,
        *,
        memory: MemoryStore | None = None,
        gemini: GeminiClient | None = None,
        llm: LocalLlm | None = None,
        openai: OpenAiClient | None = None,
        persona: str = PERSONA,
        always_awake: bool = False,
    ):
        self.memory = memory or MemoryStore()
        self.gemini = gemini or GeminiClient()
        # On-device LLM for freeform chat. Defaults to None so unit tests stay
        # offline/deterministic; the runner injects a real LocalLlm().
        self.llm = llm
        # Cloud second layer behind the local LLM (OpenAI). Local stays first.
        self.openai = openai
        self.persona = persona
        self.awake = always_awake
        self._always_awake = always_awake

    # -- top-level -----------------------------------------------------
    def route(self, text: str, *, now: datetime) -> Reply:
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

        return self._handle(command, lang, now=now)

    # -- command handling ----------------------------------------------
    def _handle(self, text: str, lang: str, *, now: datetime) -> Reply:
        if _NAME_GET.search(text):
            return self._get_name(lang)
        if _NAME_SET.search(text):
            return self._set_name(text, lang, now=now)
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
        if _NEWS.search(text) or _SITE.search(text):
            return self._lookup(text, lang)
        return self._chat(text, lang)

    def _remember(self, text: str, lang: str, *, now: datetime) -> Reply:
        body = _REMEMBER.sub("", text, count=1).strip(" ,.:!")
        body = _REMEMBER_TAIL.sub("", body).strip()
        if not body:
            return Reply(_t(lang, "Co mam zapamiętać?", "What should I remember?"), lang, "wake")
        note = self.memory.add_note(body, now=now)
        return Reply(
            _t(lang, f"Zapamiętałam: {note.text}.", f"Got it, I'll remember: {note.text}."),
            lang, "note", {"id": note.id, "text": note.text},
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

    def _lookup(self, query: str, lang: str) -> Reply:
        if not self.gemini.available:
            return Reply(
                _t(lang,
                   "Sprawdzenie wiadomości wymaga konta Gemini — ustaw GEMINI_API_KEY.",
                   "Checking the news needs a Gemini account — set GEMINI_API_KEY."),
                lang, "news", {"needs_key": True},
            )
        sys = self.persona + _t(lang, " Streść zwięźle, po polsku.", " Summarise briefly, in English.")
        try:
            answer = self.gemini.grounded(query, system=sys)
        except GeminiError as e:
            return Reply(_t(lang, f"Nie udało się sprawdzić: {e}", f"Lookup failed: {e}"), lang, "error")
        return Reply(answer, lang, "news", {"grounded": True})

    def _chat(self, text: str, lang: str) -> Reply:
        # Personalise with the user's name when known.
        system = self.persona
        name = self.memory.get_profile("name")
        if name:
            system += _t(lang, f" Użytkownik ma na imię {name}.", f" The user's name is {name}.")
        # On-device LLM first (offline, private). Then the OpenAI cloud second
        # layer, then Gemini, then the canned "needs a model/key" fallback.
        if self.llm is not None and self.llm.available:
            try:
                answer = self.llm.chat(text, system=system)
            except LlmError as e:
                return Reply(_t(lang, f"Coś poszło nie tak: {e}", f"Something went wrong: {e}"), lang, "error")
            return Reply(answer, lang, "chat", {"engine": "local"})
        if self.openai is not None and self.openai.available:
            try:
                answer = self.openai.chat(text, system=system)
            except OpenAiError as e:
                return Reply(_t(lang, f"Coś poszło nie tak: {e}", f"Something went wrong: {e}"), lang, "error")
            return Reply(answer, lang, "chat", {"engine": "openai"})
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
