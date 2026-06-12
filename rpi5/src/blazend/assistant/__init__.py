"""`blazend.assistant` — the working voice-assistant prototype (Jessica).

A self-contained, text-drivable assistant that runs on the dev host (no
audio device or local model required) and demonstrates the four prototype
capabilities:

1. **Reacts to her name** — wakes on "Jessica" / "Jess" / "hej Jessico"
   (:mod:`blazend.assistant.wake`).
2. **Converses in Polish** (and English) — :mod:`blazend.assistant.engine`
   replies in the user's language; freeform chat is backed by Gemini.
3. **Checks news / sites** via your **Gemini** account
   (:mod:`blazend.assistant.gemini`), grounded query → spoken summary.
4. **Remembers terms + events and reminds** — persistent notes + reminders
   with PL/EN time parsing (:mod:`blazend.assistant.memory`,
   :mod:`blazend.assistant.timeparse`).

Run it: ``python -m blazend.assistant`` (or ``make demo``). The Gemini-backed
features activate when ``GEMINI_API_KEY`` is set; everything else works
offline and is deterministically tested.
"""

from blazend.assistant.engine import Assistant, Reply

__all__ = ["Assistant", "Reply"]
