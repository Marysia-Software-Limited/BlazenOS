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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jessica", description="Jessica agent (Linux node).")
    ap.add_argument("prompt", nargs="?", help="one-shot: answer this utterance and exit")
    ap.add_argument("--data", help="memory JSON path (default: $BLAZEN_DATA_DIR/memory.json)")
    args = ap.parse_args(argv)

    agent = build_assistant(data=Path(args.data) if args.data else None)

    if args.prompt is not None:
        reply = agent.route(args.prompt, now=datetime.now())
        if reply.text:
            print(reply.text)
        return 0

    print("── Jessica (Linux node) ── Ctrl-D lub „wyjdź” kończy ──")
    while True:
        try:
            line = input("  ty> ")
        except EOFError:
            break
        if line.strip() in {"exit", "quit", "wyjdź", "wyjdz"}:
            break
        reply = agent.route(line, now=datetime.now())
        if reply.text:
            print(f"  Jessica: {reply.text}")
    print("\n  Jessica: Do usłyszenia!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
