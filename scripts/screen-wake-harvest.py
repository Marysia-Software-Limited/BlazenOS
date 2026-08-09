#!/usr/bin/env python3
"""Screen harvested wake-negative clips through paul's GPU whisper (large-v3).

Part of the wake-retrain loop (docs/07-CONFIGURATION.md, harvest_false_wakes):
pull `/var/lib/blazen/wake-negatives/*.wav` from the Pi into a dated harvest
dir, run this, then `train-wake.py --neg-dir ~/wake-samples/negatives`.

A clip whose transcript contains a wake-like token is a SUSPECT (a distant real
"dżesika" that the Pi's small model missed) — it must NOT be ingested as a
negative; it lands in `<harvest>/suspects/` for a human listen. Everything else
is copied into the negatives pool as `<prefix>_<orig>.wav`. A `screening.json`
with every transcript is left in the harvest dir.

Usage: scripts/screen-wake-harvest.py ~/wake-samples/harvest-YYYYMMDD \
           [--prefix harvestYYMMDD] [--neg-dir ~/wake-samples/negatives]
"""
import argparse
import json
import re
import shutil
import sys
import unicodedata
import urllib.request
import wave
from pathlib import Path

URL = "http://127.0.0.1:8090/transcribe?sr=16000&lang=pl"

# Fold diacritics and match wake-like tokens loosely: dżesika/dziesika/jessica/
# jesika/dżesiko... (the Pi's own harvest reason already says empty/notext).
WAKE_RE = re.compile(r"(dzesik|dziesik|jessic|jessik|jesik|dzesic|czesik|gesik)")


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.replace("ł", "l")


def transcribe(path: Path) -> dict:
    with wave.open(str(path), "rb") as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1, path
        pcm = w.readframes(w.getnframes())
    req = urllib.request.Request(URL, data=pcm, method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("harvest", type=Path, help="dir of pulled wake-negative wavs")
    ap.add_argument("--prefix", default="", help="ingest filename prefix (default: harvest dir name)")
    ap.add_argument("--neg-dir", type=Path, default=Path.home() / "wake-samples" / "negatives")
    args = ap.parse_args()
    src: Path = args.harvest.expanduser()
    neg: Path = args.neg_dir.expanduser()
    sus = src / "suspects"
    prefix = args.prefix or src.name.replace("-", "")
    sus.mkdir(exist_ok=True)
    neg.mkdir(parents=True, exist_ok=True)
    clips = sorted(src.glob("*.wav"))
    suspects, ingested, results = [], 0, []
    for i, clip in enumerate(clips, 1):
        try:
            out = transcribe(clip)
        except Exception as e:  # noqa: BLE001
            print(f"[{i}/{len(clips)}] {clip.name}: transcribe FAILED ({e}) — skipping", flush=True)
            continue
        text = str(out.get("text", "")).strip()
        folded = fold(text)
        hit = bool(WAKE_RE.search(folded))
        results.append({"clip": clip.name, "text": text, "suspect": hit})
        if hit:
            suspects.append((clip.name, text))
            shutil.copy2(clip, sus / clip.name)
        else:
            shutil.copy2(clip, neg / f"{prefix}_{clip.name}")
            ingested += 1
        if i % 25 == 0:
            print(f"[{i}/{len(clips)}] … {ingested} ingested, {len(suspects)} suspects", flush=True)
    (src / "screening.json").write_text(json.dumps(results, ensure_ascii=False, indent=1))
    print(f"\nDONE: {ingested} negatives ingested, {len(suspects)} suspects excluded")
    for name, text in suspects:
        print(f"  SUSPECT {name}: {text!r}")
    # Clips with ANY text at all are worth a look too (harvest reason said notext/empty)
    texty = [r for r in results if r["text"] and not r["suspect"]]
    print(f"{len(texty)} non-suspect clips had some transcript (TV/ambient speech — good negatives)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
