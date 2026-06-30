#!/usr/bin/env python3
"""Jabra voice-recognition check — record from the Jabra SPEAK 410 USB
mic and transcribe with faster-whisper.

This is a *hardware* diagnostic: it proves the real capture path on the
Jabra (USB-audio-class) speakerphone plus on-device, multilingual
(Polish-first) speech recognition end to end, independent of the appliance's
own `blazend-asr` unit. Models run on the Pi 5 CPU (int8); nothing leaves
the device.

Usage (speak when it says "MÓW TERAZ / SPEAK NOW"):

    .venv/bin/python scripts/jabra-voice-check.py                 # 5 s, pl, small
    .venv/bin/python scripts/jabra-voice-check.py --lang auto     # let it detect
    .venv/bin/python scripts/jabra-voice-check.py --seconds 7 --model medium
    .venv/bin/python scripts/jabra-voice-check.py --loop          # keep listening

The Jabra captures mono 16 kHz natively (card id `USB`). Default device is
`plughw:CARD=USB,DEV=0`; override with --device for another mic.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import time
import wave

import numpy as np

DEFAULT_DEVICE = "plughw:CARD=USB,DEV=0"


def record(device: str, seconds: int, path: str) -> None:
    """Capture mono 16 kHz S16_LE — the format faster-whisper wants."""
    subprocess.run(
        ["arecord", "-D", device, "-f", "S16_LE", "-r", "16000", "-c", "1",
         "-d", str(seconds), path],
        check=True,
        stderr=subprocess.DEVNULL,
    )


def rms(path: str) -> float:
    with wave.open(path, "rb") as w:
        a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)
    return float(np.sqrt((a**2).mean())) if a.size else 0.0


def main() -> int:
    ap = argparse.ArgumentParser(prog="jabra-voice-check")
    ap.add_argument("--seconds", type=int, default=5, help="recording length")
    ap.add_argument("--lang", default="pl", help="pl | en | auto | <iso>")
    ap.add_argument("--model", default="small", help="small | medium | large-v3-turbo | ...")
    ap.add_argument("--device", default=DEFAULT_DEVICE)
    ap.add_argument("--loop", action="store_true", help="keep listening until Ctrl-C")
    args = ap.parse_args()

    from faster_whisper import WhisperModel

    print(f"Loading Whisper '{args.model}' on CPU (int8) — first run downloads it…", flush=True)
    t0 = time.monotonic()
    model = WhisperModel(args.model, device="cpu", compute_type="int8")
    print(f"  model ready in {time.monotonic() - t0:.1f}s\n", flush=True)

    force_lang = None if args.lang == "auto" else args.lang
    try:
        while True:
            print(f"🎙  MÓW TERAZ / SPEAK NOW — recording {args.seconds}s "
                  f"from {args.device} …", flush=True)
            with tempfile.NamedTemporaryFile(suffix=".wav") as tf:
                record(args.device, args.seconds, tf.name)
                level = rms(tf.name)
                t0 = time.monotonic()
                segments, info = model.transcribe(tf.name, language=force_lang, beam_size=5)
                text = "".join(s.text for s in segments).strip()
                dt = time.monotonic() - t0
            flag = "  (very quiet — check mic gain)" if level < 150 else ""
            print(f"  capture RMS={level:.0f}{flag}")
            print(f"  detected lang={info.language} p={info.language_probability:.2f}  "
                  f"asr={dt:.1f}s")
            print(f"  >>> {text!r}\n", flush=True)
            if not args.loop:
                break
    except KeyboardInterrupt:
        print("\nbye")
    return 0


if __name__ == "__main__":
    sys.exit(main())
