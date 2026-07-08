#!/usr/bin/env python3
"""Pre-render Jessica's fixed phrases to the offline voice cache via XTTS.

Reads `configs/phrases.yaml`, renders each cue/phrase in Jessica's XTTS voice
(`Ana Florence`, via the mesh), and writes **raw mono i16 PCM at the TTS ring rate
(22050 Hz)** to `<cache>/<lang>/<fnv(text|lang|speaker)>.pcm` — the exact key +
format `blazend-tts` reads (`fnv1a_hex` / `cache_path` in its main.rs). So her rich
voice is available OFFLINE (no paul dependency at speak time); Piper stays the floor.

Run on paul (needs the GPU XTTS). Then distribute the cache to each node (scp, or
`make voicebank`), e.g. to the Pi's `/var/lib/blazen/voice-cache/`.

    scripts/render-voicebank.py --cache ~/voice-cache
"""
from __future__ import annotations

import argparse
import array
import io
import struct
import sys
import wave
from pathlib import Path

import yaml

from jessica_linux.voice import Voice

RING_RATE = 22_050
SPEAKER = "Ana Florence"


def fnv1a_hex(s: str) -> str:
    """FNV-1a 64-bit hex — MUST match blazend-tts `fnv1a_hex` in main.rs."""
    h = 0xCBF29CE484222325
    for b in s.encode("utf-8"):
        h ^= b
        h = (h * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return f"{h:016x}"


def _resample_i16(samples: list[int], src: int, dst: int) -> list[int]:
    """Linear resample — MUST match blazend-tts `resample_i16`."""
    if src == dst or not samples:
        return samples
    ratio = src / dst
    out_len = int(len(samples) / ratio)
    last = len(samples) - 1
    out: list[int] = []
    for i in range(out_len):
        pos = i * ratio
        idx = int(pos)
        frac = pos - idx
        a = samples[min(idx, last)]
        b = samples[min(idx + 1, last)]
        out.append(int(round(a + (b - a) * frac)))
    return out


def _wav_to_ring_pcm(wav_bytes: bytes, dst_rate: int) -> list[int]:
    with wave.open(io.BytesIO(wav_bytes)) as w:
        src_rate, ch, frames = w.getframerate(), w.getnchannels(), w.readframes(w.getnframes())
    samples = list(struct.unpack(f"<{len(frames) // 2}h", frames))
    if ch == 2:
        samples = [(samples[i] + samples[i + 1]) // 2 for i in range(0, len(samples) - 1, 2)]
    return _resample_i16(samples, src_rate, dst_rate)


def _phrases(data: dict) -> list[tuple[str, str]]:
    """(lang, text) for every cue + any `phrases:` list, PL + EN."""
    out: list[tuple[str, str]] = []
    for group in ("cues",):
        for _name, langs in (data.get(group) or {}).items():
            for lang, text in (langs or {}).items():
                if text:
                    out.append((str(lang), str(text)))
    for item in data.get("phrases", []) or []:
        for lang, text in (item or {}).items():
            if text:
                out.append((str(lang), str(text)))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Pre-render Jessica's phrases to the XTTS voice cache.")
    ap.add_argument("--phrases", default="configs/phrases.yaml")
    ap.add_argument("--cache", default=str(Path.home() / "voice-cache"))
    ap.add_argument("--speaker", default=SPEAKER)
    ap.add_argument("--force", action="store_true", help="re-render even if cached")
    args = ap.parse_args()

    data = yaml.safe_load(Path(args.phrases).read_text(encoding="utf-8")) or {}
    voice = Voice()
    if not voice.available:
        print("no XTTS resource in the mesh — cannot render the voicebank", file=sys.stderr)
        return 1

    cache = Path(args.cache)
    done = skipped = 0
    for lang, text in _phrases(data):
        out = cache / lang / f"{fnv1a_hex(f'{text}|{lang}|{args.speaker}')}.pcm"
        if out.exists() and not args.force:
            skipped += 1
            continue
        pcm = _wav_to_ring_pcm(voice.render(text), RING_RATE)
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".pcm.tmp")
        with tmp.open("wb") as f:
            array.array("h", pcm).tofile(f)  # i16 LE on x86 — matches blazend-tts
        tmp.replace(out)
        print(f"  {lang} {text!r} → {out.name} ({len(pcm)} samples)")
        done += 1
    print(f"voicebank: {done} rendered, {skipped} already cached → {cache}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
