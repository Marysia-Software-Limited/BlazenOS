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
from blazend.assistant.memory import MemoryStore
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
    r"\b(jakie\s+mam\s+przypomnienia|moje\s+przypomnienia|list\s+reminders|"
    r"what\s+reminders|my\s+reminders)\b",
    re.IGNORECASE,
)
_REMIND = re.compile(r"\b(przypomnij(\s+mi)?|remind\s+me)\b", re.IGNORECASE)
_REMEMBER = re.compile(r"\b(zapamiętaj|zapamietaj|zapisz|remember)\b", re.IGNORECASE)
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
_REMIND_TAIL = re.compile(r"^(mi\s+)?(żeby|zeby|o\s+tym,?\s+że|to|that|abym|aby)\s+", re.IGNORECASE)
# A time expression to split off a reminder's task text.
_TIME_SPAN = re.compile(
    r"\b((za|in)\s+(\d+\s+)?\w+|((o|at)\s+\d{1,2}(:\d{2})?\s*(am|pm)?))\b"
    r"|\b(jutro|tomorrow)\b",
    re.IGNORECASE,
)


@dataclass
class Reply:
    """The assistant's response to one utterance."""

    text: str
    language: str = "pl"
    action: str = "chat"  # wake | asleep | note | reminder | recall | news | chat | error
    data: dict[str, Any] = field(default_factory=dict)


class Assistant:
    """Stateful conversation engine. ``route()`` handles one utterance."""

    def __init__(
        self,
        *,
        memory: MemoryStore | None = None,
        gemini: GeminiClient | None = None,
        persona: str = PERSONA,
        always_awake: bool = False,
    ):
        self.memory = memory or MemoryStore()
        self.gemini = gemini or GeminiClient()
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
            return Reply(_t(lang, "Tak? Słucham.", "Yes? I'm listening."), lang, "wake")

        return self._handle(command, lang, now=now)

    # -- command handling ----------------------------------------------
    def _handle(self, text: str, lang: str, *, now: datetime) -> Reply:
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

    def _remind(self, text: str, lang: str, *, now: datetime) -> Reply:
        when = parse_when(text, now)
        task = _REMIND.sub("", text, count=1).strip(" ,.:!")
        task = _REMIND_TAIL.sub("", task).strip()
        task = _TIME_SPAN.sub("", task).strip(" ,.:!").strip()
        if not when:
            return Reply(
                _t(lang, "Na kiedy mam przypomnieć? Np. „o 15:00” albo „za 10 minut”.",
                   "When should I remind you? E.g. \"at 3pm\" or \"in 10 minutes\"."),
                lang, "wake",
            )
        if not task:
            task = _t(lang, "przypomnienie", "reminder")
        rem = self.memory.add_reminder(task, due=when, now=now)
        return Reply(
            _t(lang, f"Przypomnę Ci o {when.strftime('%H:%M')}: {task}.",
               f"I'll remind you at {when.strftime('%H:%M')}: {task}."),
            lang, "reminder", {"id": rem.id, "due": rem.due, "text": task},
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
        if not self.gemini.available:
            return Reply(
                _t(lang,
                   "Mogę rozmawiać swobodnie po ustawieniu GEMINI_API_KEY (albo lokalnego modelu).",
                   "I can chat freely once GEMINI_API_KEY (or a local model) is set."),
                lang, "chat", {"needs_key": True},
            )
        try:
            answer = self.gemini.chat(text, system=self.persona)
        except GeminiError as e:
            return Reply(_t(lang, f"Coś poszło nie tak: {e}", f"Something went wrong: {e}"), lang, "error")
        return Reply(answer, lang, "chat")

    # -- reminders due -------------------------------------------------
    def due_reminders(self, now: datetime) -> list[Reply]:
        """Fire any due reminders as spoken replies (call on a timer tick)."""
        out: list[Reply] = []
        for r in self.memory.due(now):
            out.append(Reply(f"⏰ {r.text}", "pl", "reminder", {"id": r.id, "fired": True}))
        return out
