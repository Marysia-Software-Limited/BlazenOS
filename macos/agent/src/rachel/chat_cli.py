"""rachel-chat — talk to Dżesika on the Mac (node-local MLX, shared context).

    rachel-chat "poleć mi książkę na wieczór"   # one-shot
    rachel-chat                                  # REPL (blank line / Ctrl-D exits)

Routes to rachel's own MLX via the mesh (Bielik-11B for quick turns, Qwen2.5-72B
for recommend/open reasoning), speaks Jessica's Polish persona, and recalls recent
shared-context notes from the fabric. No blazend dependency — pure domains.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from context_sync import context_paths

from rachel.chat import RachelChat


def _memory_path() -> Path:
    mem, _ = context_paths(None)  # honors BLAZEN_DATA_DIR (rachel: ~/audiobooks)
    return mem


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="rachel-chat")
    ap.add_argument("prompt", nargs="*", help="one-shot question; omit for a REPL")
    a = ap.parse_args(argv)
    chat = RachelChat(memory_path=_memory_path())

    if a.prompt:
        print(chat.ask(" ".join(a.prompt)))
        return 0

    print("Dżesika (Mac) — pisz po polsku. Pusta linia kończy.", file=sys.stderr)
    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            return 0
        print(chat.ask(line))


if __name__ == "__main__":
    raise SystemExit(main())
