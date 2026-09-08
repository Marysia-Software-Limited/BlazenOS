#!/usr/bin/env python3
"""Calibrate the speaker-ID threshold from real site audio (2026-09-08).

Owner set: the voice-training recordings (default: the desktop layout's
voice-training dir). Impostor set: the harvested false-wake clips — REAL
other-people audio from this very room (radio, podcasts, guests). Leave-one-
out over the owner set gives genuine scores; every impostor clip vs the full
owner set gives impostor scores. Prints the score distributions, the EER
point and a recommended site threshold (midpoint biased toward accepting the
owner — a missed owner command costs more than one earcon).

Usage:
  scripts/speaker-calibrate.py [--owner DIR] [--impostor DIR] [--model PATH]
"""

from __future__ import annotations

import argparse
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "rpi5" / "src"))
from blazend.domains.voice_input.adapters.rpi5.asr.speaker import (  # noqa: E402
    SpeakerId, cosine, speech_bursts,
)


def _load(p: Path) -> np.ndarray | None:
    try:
        with wave.open(str(p)) as w:
            if w.getframerate() != 16_000 or w.getnchannels() != 1:
                return None
            return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    except (OSError, wave.Error):
        return None


def main() -> int:
    home = Path.home()
    ap = argparse.ArgumentParser()
    ap.add_argument("--owner", type=Path,
                    default=home / ".local/share/blazen/voice-training")
    ap.add_argument("--impostor", type=Path,
                    default=home / ".local/share/blazen/wake-negatives")
    ap.add_argument("--model", type=Path, default=None)
    args = ap.parse_args()

    sid = SpeakerId(model_path=args.model) if args.model else SpeakerId()
    own_embs = []
    for p in sorted(args.owner.glob("*.wav")):
        pcm = _load(p)
        if pcm is None:
            continue
        for k, burst in enumerate(speech_bursts(pcm)):
            e = sid.embed(burst)
            if e is not None:
                own_embs.append((f"{p.name}#{k}", e))
    if len(own_embs) < 3:
        print(f"not enough owner recordings in {args.owner}")
        return 1

    genuine = []
    for i, (_, e) in enumerate(own_embs):
        rest = [x for j, (_, x) in enumerate(own_embs) if j != i]
        genuine.append(max(cosine(e, r) for r in rest))
    impostor = []
    for p in sorted(args.impostor.glob("*.wav")):
        pcm = _load(p)
        e = sid.embed(pcm) if pcm is not None else None
        if e is not None:
            impostor.append(max(cosine(e, x) for _, x in own_embs))

    g, im = np.asarray(genuine), np.asarray(impostor)
    print(f"owner:    n={len(g)}  min={g.min():.3f} p10={np.percentile(g, 10):.3f} "
          f"median={np.median(g):.3f}")
    print(f"impostor: n={len(im)} max={im.max():.3f} p90={np.percentile(im, 90):.3f} "
          f"median={np.median(im):.3f}")
    thresholds = np.linspace(0.1, 0.9, 161)
    far = [(im >= t).mean() for t in thresholds]
    frr = [(g < t).mean() for t in thresholds]
    eer_i = int(np.argmin(np.abs(np.asarray(far) - np.asarray(frr))))
    print(f"EER ≈ {(far[eer_i] + frr[eer_i]) / 2:.1%} at threshold {thresholds[eer_i]:.2f}")
    # Recommend: below the weakest genuine score (accept the owner always),
    # but above the strongest impostor if the sets separate cleanly.
    rec = min(g.min() - 0.05, max(float(im.max()) + 0.03, thresholds[eer_i]))
    print(f"recommended site threshold: {rec:.2f} "
          f"(owner-min {g.min():.2f} vs impostor-max {im.max():.2f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
