#!/usr/bin/env python3
"""Queue a spoken announcement on a Jessica node (2026-09-22).

Drops ``{"text", "lang", "volume_pct"}`` as a JSON file into the node's
``<runtime>/announce/`` spool; the orchestrator's announce channel speaks it
through the normal TTS path (see supervisor._announce_channel). The runtime
dir is writable only by the service user/root — being able to write there
(locally, or over SSH + sudo for a remote node) IS the authorisation.

Local:   scripts/announce.py "Obiad gotowy!"
Remote:  scripts/announce.py --host jessica-ts "Obiad gotowy!" --volume 65
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("text", help="what Jessica should say")
    ap.add_argument("--host", default="",
                    help="SSH host of a remote node (e.g. jessica-ts); empty = this machine")
    ap.add_argument("--lang", default="pl")
    ap.add_argument("--volume", type=int, default=None,
                    help="temporary output volume %% for this announcement")
    ap.add_argument("--runtime-dir", default="",
                    help="override the node's runtime dir (default: /run/blazen "
                         "remotely, $BLAZEN_RUNTIME_DIR or /run/user/<uid>/blazen locally)")
    args = ap.parse_args()

    req: dict[str, object] = {"text": args.text, "lang": args.lang}
    if args.volume is not None:
        req["volume_pct"] = args.volume
    payload = json.dumps(req, ensure_ascii=False)
    name = f"ann-{int(time.time())}-{uuid.uuid4().hex[:6]}.json"

    if args.host:
        rt = args.runtime_dir or "/run/blazen"
        # Write hidden, then rename: the watcher globs *.json, so it can never
        # see a half-written file.
        script = (f"cat > {rt}/announce/.{name} && "
                  f"mv {rt}/announce/.{name} {rt}/announce/{name}")
        subprocess.run(["ssh", "-o", "BatchMode=yes", args.host,
                        "sudo", "-n", "bash", "-c", script],
                       input=payload.encode(), check=True)
    else:
        rt = Path(args.runtime_dir or os.environ.get("BLAZEN_RUNTIME_DIR")
                  or f"/run/user/{os.getuid()}/blazen")
        spool = rt / "announce"
        spool.mkdir(parents=True, exist_ok=True)
        tmp = spool / f".{name}"
        tmp.write_text(payload, encoding="utf-8")
        tmp.rename(spool / name)
    print(f"queued {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
