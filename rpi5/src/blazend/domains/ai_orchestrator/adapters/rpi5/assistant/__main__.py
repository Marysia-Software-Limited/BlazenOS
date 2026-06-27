"""`python -m blazend.domains.ai_orchestrator.adapters.rpi5.assistant` — the Jessica prototype REPL.

A text stand-in for the voice loop: you type what you'd say, Jessica
prints what she'd speak. Reacts to her name, converses in Polish, checks
news/sites via Gemini (when ``GEMINI_API_KEY`` is set), and remembers
terms + reminds you of events. Memory persists under ``BLAZEN_DATA_DIR``.

  make demo
  python -m blazend.domains.ai_orchestrator.adapters.rpi5.assistant --once "Hej Jessico, przypomnij mi o praniu za 5 sekund"
"""

from __future__ import annotations

import argparse
import sys
import threading
from datetime import datetime
from pathlib import Path

from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.engine import Assistant, Reply
from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.gemini import GeminiClient
from blazend.domains.context.adapters.rpi5.memory import MemoryStore


def _build(args: argparse.Namespace) -> Assistant:
    mem = MemoryStore(Path(args.data) if args.data else None)
    return Assistant(memory=mem, gemini=GeminiClient(), always_awake=args.no_wake)


def _banner(a: Assistant) -> str:
    gem = "Gemini: aktywny ✅" if a.gemini.available else "Gemini: brak klucza (ustaw GEMINI_API_KEY)"
    return (
        "── Jessica (prototyp) ──────────────────────────────\n"
        f"{gem}   |   pamięć: {a.memory.path}\n"
        "Powiedz „Jessica”, aby mnie obudzić. Spróbuj:\n"
        "  • Hej Jessico, zapamiętaj że kod do bramy to 4729\n"
        "  • Jessica, przypomnij mi o praniu za 5 sekund\n"
        "  • Jessica, co w wiadomościach?\n"
        "  • co pamiętasz?\n"
        "Ctrl-D, aby zakończyć.\n"
        "────────────────────────────────────────────────────"
    )


def _say(reply: Reply) -> None:
    if reply.text:
        print(f"  Jessica: {reply.text}")


def _reminder_watcher(a: Assistant, stop: threading.Event) -> None:
    while not stop.wait(1.0):
        for r in a.due_reminders(datetime.now()):
            print(f"\n  Jessica: {r.text}\n  ty> ", end="", flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="blazend.domains.ai_orchestrator.adapters.rpi5.assistant")
    ap.add_argument("--once", help="route a single utterance and exit (non-interactive)")
    ap.add_argument("--no-wake", action="store_true", help="skip the wake gate (always listening)")
    ap.add_argument("--data", help="memory JSON path (default: $BLAZEN_DATA_DIR/memory.json)")
    args = ap.parse_args(argv)

    a = _build(args)

    if args.once is not None:
        _say(a.route(args.once, now=datetime.now()))
        return 0

    print(_banner(a))
    stop = threading.Event()
    watcher = threading.Thread(target=_reminder_watcher, args=(a, stop), daemon=True)
    watcher.start()
    try:
        while True:
            try:
                line = input("  ty> ")
            except EOFError:
                break
            if line.strip() in {"exit", "quit", "wyjdź", "wyjdz"}:
                break
            _say(a.route(line, now=datetime.now()))
    finally:
        stop.set()
    print("\n  Jessica: Do usłyszenia!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
