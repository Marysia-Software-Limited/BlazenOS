"""Tier 0 — the secondary-mic room sensor (2026-08-31).

Pure pieces only: the ambient→volume mapping and the tone analyser of
scripts/speaker-check.py (via a synthesised fixture). The capture side needs
real hardware and is exercised live by `make speaker-check` on paul.
"""

from __future__ import annotations

import math
import struct
import subprocess
import sys
import wave
from pathlib import Path

from blazend.domains.systems.adapters.rpi5.orchestrator.supervisor import (
    _ambient_target_pct,
)

REPO = Path(__file__).resolve().parents[3]

_KW = {"quiet_rms": 40.0, "loud_rms": 800.0,
       "min_pct": 40, "max_pct": 100, "step_pct": 10}


def test_quiet_room_walks_volume_toward_minimum():
    # Far target, but at most step_pct per cycle.
    assert _ambient_target_pct(10.0, current=80, **_KW) == 70
    assert _ambient_target_pct(10.0, current=70, **_KW) == 60


def test_loud_room_walks_volume_up_and_saturates():
    assert _ambient_target_pct(2000.0, current=80, **_KW) == 90
    assert _ambient_target_pct(2000.0, current=95, **_KW) == 100
    assert _ambient_target_pct(2000.0, current=100, **_KW) == 100


def test_small_nudges_are_ignored():
    # Mid-range room: target ≈ current → no churn.
    mid = 40.0 + (800.0 - 40.0) * (72 - 40) / 60  # rms that maps to ~72%
    assert _ambient_target_pct(mid, current=70, **_KW) == 70


def test_interpolation_is_monotonic():
    vols = [_ambient_target_pct(r, current=40, **_KW)
            for r in (0, 100, 300, 500, 900)]
    assert vols == sorted(vols)


def test_speaker_check_analyser_detects_tones(tmp_path):
    """Synthesise the exact tone ladder into a 'recording' and require the
    analyser to flag every bin; a noise-only file must fail."""
    sr = 16_000
    tones = tmp_path / "tones.wav"
    frames = bytearray()
    for hz in (440, 880, 1320):
        for i in range(int(sr * 1.2)):
            v = int(0.4 * 32767 * math.sin(2 * math.pi * hz * i / sr))
            frames += struct.pack("<h", v)
    with wave.open(str(tones), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(bytes(frames))
    script = REPO / "scripts" / "speaker-check.py"
    ok = subprocess.run([sys.executable, str(script), "--dry-run", str(tones)],
                       capture_output=True, text=True)
    assert ok.returncode == 0, ok.stdout

    noise = tmp_path / "noise.wav"
    import random

    rng = random.Random(7)
    quiet = bytes(b for _ in range(sr * 2)
                  for b in struct.pack("<h", rng.randint(-300, 300)))
    with wave.open(str(noise), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(quiet)
    bad = subprocess.run([sys.executable, str(script), "--dry-run", str(noise)],
                        capture_output=True, text=True)
    assert bad.returncode == 1, bad.stdout
