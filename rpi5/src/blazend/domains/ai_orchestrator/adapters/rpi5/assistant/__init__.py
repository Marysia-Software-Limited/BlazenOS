"""`blazend.domains.ai_orchestrator.adapters.rpi5.assistant` — the working voice-assistant prototype (Jessica).

A self-contained, text-drivable assistant that runs on the dev host (no
audio device or local model required) and demonstrates the four prototype
capabilities:

1. **Reacts to her name** — wakes on "Jessica" / "Jess" / "hej Jessico"
   (:mod:`blazend.domains.ai_orchestrator.adapters.rpi5.assistant.wake`).
2. **Converses in Polish** (and English) — :mod:`blazend.domains.ai_orchestrator.adapters.rpi5.assistant.engine`
   replies in the user's language; freeform chat is backed by Gemini.
3. **Checks news / sites** via your **Gemini** account
   (:mod:`blazend.domains.ai_orchestrator.adapters.rpi5.assistant.gemini`), grounded query → spoken summary.
4. **Remembers terms + events and reminds** — persistent notes + reminders
   with PL/EN time parsing (:mod:`blazend.domains.context.adapters.rpi5.memory`,
   :mod:`blazend.domains.context.adapters.rpi5.timeparse`).

Run it: ``python -m blazend.domains.ai_orchestrator.adapters.rpi5.assistant`` (or ``make demo``). The Gemini-backed
features activate when ``GEMINI_API_KEY`` is set; everything else works
offline and is deterministically tested.
"""

from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.engine import Assistant, Reply

__all__ = ["Assistant", "Reply"]
