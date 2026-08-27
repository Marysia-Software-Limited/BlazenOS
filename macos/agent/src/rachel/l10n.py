"""rachel's message catalog — the Mac node's few user-facing strings.

The Pi surface keeps its catalog in ``configs/phrases.yaml`` (see
docs/21-TRANSLATIONS.md); rachel is deliberately standalone (no blazend
dependency on the Mac), so its strings live in this tiny local dict with the
same contract: every key carries BOTH ``pl`` and ``en`` (Polish-first, EN
retained as an asset), translators edit values only — never keys or
``{placeholders}``.
"""

from __future__ import annotations

_MESSAGES: dict[str, dict[str, str]] = {
    # Jessica's system prompt fallback (llm.yaml system_prompt overrides).
    "persona": {
        "pl": "Jesteś Dżesika, asystentka głosowa. Odpowiadaj po polsku, krótko.",
        "en": "You are Jessica, a voice assistant. Answer in English, briefly.",
    },
    # No MLX backend reachable.
    "llm.unavailable": {
        "pl": "Przepraszam, w tej chwili nie mam dostępu do modelu — "
              "sprawdź, czy serwer MLX działa.",
        "en": "Sorry, I can't reach the model right now — "
              "check that the MLX server is running.",
    },
    # REPL banner (stderr).
    "repl.banner": {
        "pl": "Dżesika (Mac) — pisz po polsku. Pusta linia kończy.",
        "en": "Jessica (Mac) — type your question. An empty line exits.",
    },
}


def msg(key: str, lang: str = "pl") -> str:
    """The message ``key`` in ``lang`` (pl → en fallback → the key itself)."""
    entry = _MESSAGES.get(key, {})
    return entry.get(lang) or entry.get("pl") or key


__all__ = ["msg"]
