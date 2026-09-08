#!/usr/bin/env python3
"""Learning-loop sync + consumption (2026-09-08, phase 4) — run on paul, the
network's ML server (RTX 3090; also hosts the wake retrain and whisper).

Pulls ``learning/`` (events.jsonl + clips) from jessica-ts over the Tailscale
SSH path used by releases, merges it with paul's own, then consumes labels:

- ``corrected`` events → the corrected phrases land in
  ``<state>/learning/lexicon-boost.yaml`` (loaded by asr/lexicon.py into the
  command tier, so the fuzzy repair snaps to what the user actually says);
  the (audio, corrected-text) pairs stay in the log as a future whisper
  fine-tune set.
- ``unconfirmed``/``foreign``/``denied`` events WITH clips → candidate wake
  negatives, copied into the harvest dir for the usual whisper screening +
  ``train-wake.py`` retrain (never auto-ingested).
- ``confirmed`` events with clips → extra owner-voice enrollment: the speaker
  profile is rebuilt from the voice-training dir + these clips.

Usage:
  scripts/learn-sync.py                # local consume only
  scripts/learn-sync.py --pull         # also pull jessica-ts learning/ first
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

HOME = Path.home()
STATE = HOME / ".local/share/blazen"
LEARN = STATE / "learning"
REMOTE = "beret@100.125.134.121"
REMOTE_LEARN = "/var/lib/blazen/learning"

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "rpi5" / "src"))


def pull_remote() -> None:
    dest = LEARN / "remote-jessica-ts"
    dest.mkdir(parents=True, exist_ok=True)
    print(f"pulling {REMOTE}:{REMOTE_LEARN} …")
    proc = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=30", REMOTE,
         f"sudo -n tar czf - -C {REMOTE_LEARN} . 2>/dev/null || true"],
        capture_output=True, timeout=300)
    if proc.returncode != 0 or not proc.stdout:
        print("  (nothing pulled — host unreachable or no learning data yet)")
        return
    subprocess.run(["tar", "xzf", "-", "-C", str(dest)],
                   input=proc.stdout, check=True)
    print(f"  merged into {dest}")


def _events() -> list[dict]:
    out: list[dict] = []
    for f in [LEARN / "events.jsonl",
              LEARN / "remote-jessica-ts" / "events.jsonl"]:
        if not f.is_file():
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def consume(events: list[dict]) -> None:
    # 1) corrected → lexicon boost (dedup, cap 200 newest).
    boost_path = LEARN / "lexicon-boost.yaml"
    existing: list[str] = []
    if boost_path.is_file():
        existing = list((yaml.safe_load(
            boost_path.read_text(encoding="utf-8")) or {}).get("phrases", []))
    phrases = list(dict.fromkeys(
        existing + [str(e["corrected"]) for e in events
                    if e.get("outcome") == "corrected" and e.get("corrected")]
    ))[-200:]
    boost_path.parent.mkdir(parents=True, exist_ok=True)
    boost_path.write_text(
        "# Learned corrections — written by scripts/learn-sync.py; loaded by\n"
        "# asr/lexicon.py into the command tier. Do not edit by hand.\n"
        + yaml.safe_dump({"phrases": phrases}, allow_unicode=True),
        encoding="utf-8")
    print(f"lexicon boost: {len(phrases)} phrases")

    # 2) negative candidates → the wake harvest dir (screened at retrain).
    harvest = STATE / "wake-negatives"
    harvest.mkdir(parents=True, exist_ok=True)
    copied = 0
    for e in events:
        if e.get("outcome") not in ("unconfirmed", "foreign", "denied"):
            continue
        clip = Path(str(e.get("clip", "")))
        alt = (LEARN / "remote-jessica-ts" / "clips" / clip.name
               if clip.name else None)
        src = clip if clip.is_file() else (alt if alt and alt.is_file() else None)
        if src is None:
            continue
        dest = harvest / f"learned-{e.get('outcome')}-{src.name}"
        if not dest.exists():
            shutil.copy2(src, dest)
            copied += 1
    print(f"wake-negative candidates: +{copied} (screen via train-wake flow)")

    # 3) confirmed clips → speaker profile refresh (train dir + extras).
    from blazend.domains.voice_input.adapters.rpi5.asr.speaker import SpeakerId

    import numpy as np
    import wave

    sid = SpeakerId()
    extras = []
    for e in events:
        if e.get("outcome") != "confirmed":
            continue
        clip = Path(str(e.get("clip", "")))
        if not clip.is_file():
            continue
        try:
            with wave.open(str(clip)) as w:
                pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        except (OSError, wave.Error):
            continue
        emb = sid.embed(pcm)
        if emb is not None:
            extras.append(emb)
    if extras or (STATE / "voice-training").is_dir():
        n = sid.enroll_dir(STATE / "voice-training", extra=extras)
        print(f"speaker profile: {n} embeddings "
              f"({len(extras)} from confirmed clips)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pull", action="store_true",
                    help="pull jessica-ts learning/ over SSH first")
    args = ap.parse_args()
    if args.pull:
        pull_remote()
    events = _events()
    print(f"{len(events)} learning events")
    consume(events)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
