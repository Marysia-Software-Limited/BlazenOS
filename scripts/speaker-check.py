#!/usr/bin/env python3
"""Closed-loop speaker check: play tones on Jessica's speaker, listen on the
secondary sensor mic, and PASS/FAIL on frequency-selective detection.

Why a second mic: the Jabra SPEAK 410 is a speakerphone whose AEC removes its
own speaker from its own microphone, so "did the speaker actually play, and
how loud?" was unverifiable from inside the pipeline. A machine with a
dedicated built-in mic (paul: ALC269VC — audio.yaml `ambient_mic.device`)
closes the loop: tones are detected via their FFT bins (three orders of
magnitude over the room floor in validation, 2026-08-31), which rides out
room noise that wrecks plain RMS comparisons.

Usage:
  scripts/speaker-check.py                       # tones at current volume
  scripts/speaker-check.py --levels 50 100       # also verify volume STEPS
  scripts/speaker-check.py --dry-run room.wav    # FFT report on a wav (tests)

Exit code 0 = every tone detected (and, with --levels, higher level measured
louder). Requires: aplay/arecord, the softvol 'JessicaVol' control, and
ALSA_CONFIG_PATH pointing at jabra-asound.conf (auto-set from the site dir).
"""

from __future__ import annotations

import argparse
import math
import os
import struct
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

TONES_HZ = (440, 880, 1320)
SR_PLAY = 48_000
SR_REC = 16_000
TONE_S = 1.2
# A tone counts as heard when its bin beats the neighbourhood floor by this
# factor (validation showed ~900x; 30x leaves a wide margin for quiet rooms).
MIN_RATIO = 30.0


def _site_dir() -> Path:
    root = os.environ.get("BLAZEN_CONFIG_ROOT", "")
    if root:
        return Path(root.split(os.pathsep)[-1])
    return Path.home() / ".config" / "blazen"


def _tone_wav(path: Path) -> None:
    frames = bytearray()
    for hz in TONES_HZ:
        for i in range(int(SR_PLAY * TONE_S)):
            v = int(0.6 * 32767 * math.sin(2 * math.pi * hz * i / SR_PLAY))
            frames += struct.pack("<h", v)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR_PLAY)
        w.writeframes(bytes(frames))


def _bin_power(pcm: list[float], hz: float) -> tuple[float, float]:
    """(power at hz, median neighbourhood floor) via a Goertzel-ish DFT bin.
    Stdlib-only so the script runs on any node without numpy."""
    n = len(pcm)
    # Goertzel algorithm for a single bin.
    k = int(0.5 + n * hz / SR_REC)
    w = 2.0 * math.pi * k / n
    coeff = 2.0 * math.cos(w)
    s0 = s1 = s2 = 0.0
    for x in pcm:
        s0 = x + coeff * s1 - s2
        s2 = s1
        s1 = s0
    power = s1 * s1 + s2 * s2 - coeff * s1 * s2
    floors = []
    for off in (-97, -53, 53, 97):  # odd offsets — away from harmonics
        kk = k + off
        ww = 2.0 * math.pi * kk / n
        cc = 2.0 * math.cos(ww)
        a0 = a1 = a2 = 0.0
        for x in pcm:
            a0 = x + cc * a1 - a2
            a2 = a1
            a1 = a0
        floors.append(a1 * a1 + a2 * a2 - cc * a1 * a2)
    floors.sort()
    return power, max(1.0, floors[len(floors) // 2])


def _read_wav(path: Path) -> list[float]:
    with wave.open(str(path)) as w:
        raw = w.readframes(w.getnframes())
    return [s / 32768.0 for (s,) in struct.iter_unpack("<h", raw)]


def analyse(path: Path) -> dict[int, float]:
    """Ratio tone-bin/floor for each expected tone in the recording."""
    pcm = _read_wav(path)
    return {hz: (lambda p: p[0] / p[1])(_bin_power(pcm, hz)) for hz in TONES_HZ}


def _run_once(mic: str, alsa_env: dict[str, str], out: Path) -> dict[int, float]:
    tone = out.with_suffix(".tone.wav")
    _tone_wav(tone)
    rec = subprocess.Popen(
        ["arecord", "-q", "-D", mic, "-f", "S16_LE", "-r", str(SR_REC),
         "-c", "1", "-d", str(int(len(TONES_HZ) * TONE_S + 2)), str(out)])
    subprocess.run(["aplay", "-q", "-D", "jabra_out", str(tone)],
                   env=alsa_env, check=True)
    rec.wait(timeout=30)
    return analyse(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mic", default="plughw:CARD=Generic,DEV=0")
    ap.add_argument("--levels", nargs="*", type=int, default=[],
                    help="JessicaVol percentages to compare (e.g. 50 100)")
    ap.add_argument("--dry-run", metavar="WAV",
                    help="analyse an existing recording and exit")
    args = ap.parse_args()

    if args.dry_run:
        ratios = analyse(Path(args.dry_run))
        for hz, r in ratios.items():
            print(f"{hz} Hz: ratio {r:.1f} {'OK' if r >= MIN_RATIO else 'weak'}")
        return 0 if all(r >= MIN_RATIO for r in ratios.values()) else 1

    alsa_env = dict(os.environ)
    alsa_env.setdefault(
        "ALSA_CONFIG_PATH",
        f"/usr/share/alsa/alsa.conf:{_site_dir() / 'jabra-asound.conf'}")

    levels = args.levels or [None]  # type: ignore[list-item]
    results: list[tuple[int | None, dict[int, float]]] = []
    with tempfile.TemporaryDirectory(prefix="speaker-check-") as td:
        for i, lvl in enumerate(levels):
            if lvl is not None:
                subprocess.run(["amixer", "-c", "USB", "sset", "JessicaVol",
                                f"{lvl}%"], capture_output=True, check=False)
            results.append((lvl, _run_once(args.mic, alsa_env,
                                           Path(td) / f"rec{i}.wav")))

    ok = True
    for lvl, ratios in results:
        label = f"@{lvl}%" if lvl is not None else "@current"
        for hz, r in ratios.items():
            heard = r >= MIN_RATIO
            ok &= heard
            print(f"{label} {hz} Hz: ratio {r:.1f} {'OK' if heard else 'FAIL'}")
    if len(results) == 2 and all(lvl is not None for lvl, _ in results):
        lo, hi = results[0][1], results[1][1]
        gain = sum(math.log10(max(hi[h], 1) / max(lo[h], 1)) for h in TONES_HZ)
        db = 10 * gain / len(TONES_HZ)
        print(f"level step {results[0][0]}% → {results[1][0]}%: {db:+.1f} dB")
        ok &= db > 1.0  # louder level must measure louder
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
