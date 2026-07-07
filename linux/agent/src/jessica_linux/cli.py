"""`jessica` — text one-shot / REPL for the Jessica agent on a Linux node.

    BLAZEN_NODE=paul jessica "co potrafisz?"     # one-shot, prints the reply
    BLAZEN_NODE=paul jessica                      # interactive REPL

The heavy lifting is the shared `Assistant` engine; this is just the server-node
front door (no wake word). LLM/TTS/ASR come from the mesh (see node.py).
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from jessica_linux.node import build_assistant
from jessica_linux.voice import Voice


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jessica", description="Jessica agent (Linux node).")
    ap.add_argument("prompt", nargs="?", help="one-shot: answer this utterance and exit")
    ap.add_argument("--data", help="memory JSON path (default: $BLAZEN_DATA_DIR/memory.json)")
    ap.add_argument("--speak", metavar="TEXT",
                    help="render+play this text via the node's mesh TTS (XTTS) and exit")
    ap.add_argument("--voice", action="store_true",
                    help="also speak replies aloud via the mesh TTS, not just print them")
    args = ap.parse_args(argv)

    # --speak is pure TTS: no LLM/agent needed.
    if args.speak is not None:
        Voice().speak(args.speak)
        return 0

    agent = build_assistant(data=Path(args.data) if args.data else None)
    voice = Voice() if args.voice else None

    def respond(text: str, *, prefix: str = "") -> None:
        if not text:
            return
        print(f"{prefix}{text}")
        if voice is not None:
            voice.speak(text)

    if args.prompt is not None:
        respond(agent.route(args.prompt, now=datetime.now()).text)
        return 0

    print("── Jessica (Linux node) ── Ctrl-D lub „wyjdź” kończy ──")
    while True:
        try:
            line = input("  ty> ")
        except EOFError:
            break
        if line.strip() in {"exit", "quit", "wyjdź", "wyjdz"}:
            break
        respond(agent.route(line, now=datetime.now()).text, prefix="  Jessica: ")
    print("\n  Jessica: Do usłyszenia!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
