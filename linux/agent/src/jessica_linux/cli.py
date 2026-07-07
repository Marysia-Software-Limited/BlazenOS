"""`jessica` — text one-shot / REPL for the Jessica agent on a Linux node.

    BLAZEN_NODE=paul jessica "co potrafisz?"     # one-shot, prints the reply
    BLAZEN_NODE=paul jessica                      # interactive REPL

The heavy lifting is the shared `Assistant` engine; this is just the server-node
front door (no wake word). LLM/TTS/ASR come from the mesh (see node.py).
"""
from __future__ import annotations

import argparse
import os
from datetime import datetime
from pathlib import Path

from jessica_linux.fabric import pull_and_merge, serve
from jessica_linux.node import build_assistant
from jessica_linux.voice import Voice


def _node() -> str:
    return os.environ.get("BLAZEN_NODE") or "linux"


def _context_paths(data: str | None) -> tuple[Path, Path]:
    """This node's memory.json + a sibling progress.json (shared-context stores)."""
    from blazend.domains.context.adapters.rpi5.memory import MemoryStore

    mem = MemoryStore(Path(data) if data else None).path
    return mem, mem.parent / "progress.json"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jessica", description="Jessica agent (Linux node).")
    ap.add_argument("prompt", nargs="?", help="one-shot: answer this utterance and exit")
    ap.add_argument("--data", help="memory JSON path (default: $BLAZEN_DATA_DIR/memory.json)")
    ap.add_argument("--speak", metavar="TEXT",
                    help="render+play this text via the node's mesh TTS (XTTS) and exit")
    ap.add_argument("--voice", action="store_true",
                    help="also speak replies aloud via the mesh TTS, not just print them")
    ap.add_argument("--sync", action="store_true",
                    help="pull peers' Jessica context from the mesh and merge it locally")
    ap.add_argument("--serve-fabric", action="store_true",
                    help="serve this node's context snapshot (:7475) so peers can pull it")
    ap.add_argument("--fleet", metavar="ACTION",
                    choices=["status", "start", "stop", "restart", "verify", "serve"],
                    help="manage this node's GPU service fleet (paul)")
    args = ap.parse_args(argv)

    if args.fleet:
        from jessica_linux import fleet
        return fleet.cli(args.fleet, node=_node())

    # --speak is pure TTS: no LLM/agent needed.
    if args.speak is not None:
        Voice().speak(args.speak)
        return 0

    if args.serve_fabric:
        mem, prog = _context_paths(args.data)
        print(f"serving {_node()} context snapshot on :7475/fabric/snapshot (Ctrl-C stops)")
        serve(node=_node(), memory_path=mem, progress_path=prog)
        return 0

    if args.sync:
        mem, prog = _context_paths(args.data)
        s = pull_and_merge(node=_node(), memory_path=mem, progress_path=prog)
        print(f"synced: pulled {s['pulled'] or 'nobody'} — "
              f"{s['notes']} notes, {s['reminders']} reminders, {s['books']} books")
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
