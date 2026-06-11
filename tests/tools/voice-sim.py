#!/usr/bin/env python3
"""tests/tools/voice-sim.py — interactive REPL for poking at the pipeline.

Useful when you're hacking on blazend-* and want to play one utterance,
watch what the orchestrator does, and read the assistant's reply without
rebuilding a scenario. Intended for the VM (over SSH) and the real Pi.

Usage:
    voice-sim.py --host blazen.local
    > say "what time is it"
    > inject crash blazend-brain
    > get state ssh.enabled
    > stream wake
"""
from __future__ import annotations

import argparse
import cmd
import sys


class VoiceSim(cmd.Cmd):
    intro = "voice-sim — type 'help' for commands"
    prompt = "> "

    def __init__(self, host: str):
        super().__init__()
        self.host = host

    def do_say(self, arg: str) -> None:
        """say "<phrase>"   — synthesize and inject as user audio"""
        print(f"TODO: would synth + inject {arg!r} on {self.host}")

    def do_inject(self, arg: str) -> None:
        """inject crash <unit>   — kill a blazend-* unit"""
        print(f"TODO: inject {arg!r}")

    def do_get(self, arg: str) -> None:
        """get state <dotted.path>   — read /run/blazen/state.json"""
        print(f"TODO: get {arg!r}")

    def do_stream(self, arg: str) -> None:
        """stream <topic>   — tail one IPC topic (wake/asr/brain/tts/...)"""
        print(f"TODO: stream {arg!r}")

    def do_EOF(self, arg: str) -> bool:  # Ctrl-D
        print()
        return True

    def do_quit(self, arg: str) -> bool:
        return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="localhost", help="SSH target")
    args = ap.parse_args()
    try:
        VoiceSim(args.host).cmdloop()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
