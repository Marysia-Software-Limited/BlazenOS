"""Remote GPU XTTS-v2 server for high-quality Polish audiobook rendering.

Runs on the dev host (paul, RTX 3090 — the same box that serves Bielik via
Ollama at :11434 and whisper at :8090). The rachel (macOS) audiobook ingest
POSTs chapter text here and gets back a WAV rendered by Coqui **XTTS-v2** on the
GPU — far more natural Polish than Apple's on-device voices, and free (your GPU)
instead of per-char cloud. Same "cloud/GPU only at render time" pattern as the
whisper offload and the DSPy-compile step; Pi runtime playback stays local MP3.

Run:  BLAZEN_XTTS_SPEAKER="Ana Florence" .venv-xtts/bin/python \
        scripts/xtts_server.py            # listens on 0.0.0.0:8091
POST: /synthesize   JSON {"text": "...", "language": "pl", "speaker": "<opt>"}
      -> audio/wav  (24 kHz mono int16)

Voice cloning: set BLAZEN_XTTS_SPEAKER_WAV=/path/to/reference.wav (~6-20 s of a
narrator you like) to clone that timbre instead of a built-in speaker.

Model license: XTTS-v2 ships under the Coqui Public Model License (CPML,
non-commercial). Fine for personal use; swap to F5-TTS (MIT) if you need
commercial terms.
"""
from __future__ import annotations

import io
import os
import wave

import numpy as np
import torch
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from TTS.api import TTS

MODEL = os.environ.get("BLAZEN_XTTS_MODEL", "tts_models/multilingual/multi-dataset/xtts_v2")
DEVICE = os.environ.get("BLAZEN_XTTS_DEVICE", "cuda" if torch.cuda.is_available() else "cpu")
DEFAULT_SPEAKER = os.environ.get("BLAZEN_XTTS_SPEAKER", "Ana Florence")
SPEAKER_WAV = os.environ.get("BLAZEN_XTTS_SPEAKER_WAV", "")  # clone a reference voice if set

print(f"loading XTTS-v2 ({MODEL}) on {DEVICE}…", flush=True)
tts = TTS(model_name=MODEL, progress_bar=False).to(DEVICE)
SR = int(getattr(tts.synthesizer, "output_sample_rate", 24000))
print(f"ready (sample_rate={SR}, speaker={'wav:' + SPEAKER_WAV if SPEAKER_WAV else DEFAULT_SPEAKER})",
      flush=True)


# -- babble trim ---------------------------------------------------------------
# XTTS-v2 hallucinates trailing babble — extra speech-like garbage appended after
# the real sentence (~2 of 4 renders on short prompts; also on long replies).
# Measured on Ana Florence Polish renders: real speech paces a stable ~0.085 s per
# input char (+ ~1.2 s of leading/inter-sentence air), while babble arrives as
# EXTRA trailing speech segments after a >=0.35 s gap, blowing the total well past
# that estimate. So: segment the render by silence, and drop trailing segments
# while the audio still runs long past the text-length estimate. The first
# segment is never dropped, and a render that fits the estimate is untouched —
# slow-but-real speech survives, only the overshoot is cut.
_TRIM_SEC_PER_CHAR = 0.085  # empirical Ana-Florence pace (validated ±5 % on 4 texts)
_TRIM_BASE_S = 1.2          # leading + inter-sentence air
_TRIM_MARGIN = 1.05         # tolerated overshoot before trimming kicks in
_TRIM_SLACK_S = 0.3
_TRIM_GAP_S = 0.35          # min silence between segments
_TRIM_RMS = 0.012           # speech threshold on -1..1 floats (~400 on int16)


def _speech_segments(samples: np.ndarray, sr: int) -> list[tuple[float, float]]:
    """(start_s, end_s) of speech-active spans separated by >=_TRIM_GAP_S silence."""
    win = max(1, int(0.05 * sr))
    n = len(samples) // win
    if n == 0:
        return []
    rms = np.sqrt((samples[: n * win].reshape(n, win) ** 2).mean(axis=1))
    active = rms > _TRIM_RMS
    segs: list[tuple[float, float]] = []
    start, gap = None, 0
    gap_wins = max(1, int(_TRIM_GAP_S / 0.05))
    for i, a in enumerate(active):
        if a:
            if start is None:
                start = i
            gap = 0
        elif start is not None:
            gap += 1
            if gap >= gap_wins:
                segs.append((start * 0.05, (i - gap + 1) * 0.05))
                start = None
    if start is not None:
        segs.append((start * 0.05, n * 0.05))
    return segs


def trim_babble(samples: np.ndarray, sr: int, text: str) -> np.ndarray:
    """Cut hallucinated trailing babble; return the render otherwise unchanged."""
    limit = _TRIM_MARGIN * (_TRIM_SEC_PER_CHAR * len(text) + _TRIM_BASE_S) + _TRIM_SLACK_S
    segs = _speech_segments(samples, sr)
    if len(segs) < 2 or segs[-1][1] <= limit:
        return samples
    keep = list(segs)
    while len(keep) > 1 and keep[-1][1] > limit:
        keep.pop()
    end = min(len(samples), int((keep[-1][1] + 0.15) * sr))
    out = np.array(samples[:end], dtype=np.float32)
    fade = min(len(out), max(1, int(0.03 * sr)))
    out[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)
    print(f"babble-trimmed {segs[-1][1]:.1f}s -> {keep[-1][1]:.1f}s "
          f"(limit {limit:.1f}s, {len(text)} chars): {text[:60]!r}", flush=True)
    return out


def _wav_bytes(samples: np.ndarray, sr: int) -> bytes:
    pcm = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    pcm16 = (pcm * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm16.tobytes())
    return buf.getvalue()


async def synthesize(request: Request) -> Response:
    body = await request.json()
    text = (body.get("text") or "").strip()
    language = body.get("language", "pl")
    speaker = body.get("speaker") or DEFAULT_SPEAKER
    if not text:
        return JSONResponse({"error": "empty text"}, status_code=400)
    kwargs = {"text": text, "language": language, "split_sentences": True}
    if SPEAKER_WAV:
        kwargs["speaker_wav"] = SPEAKER_WAV
    else:
        kwargs["speaker"] = speaker
    wav = tts.tts(**kwargs)
    samples = trim_babble(np.asarray(wav, dtype=np.float32), SR, text)
    return Response(_wav_bytes(samples, SR), media_type="audio/wav")


async def health(_request: Request) -> JSONResponse:
    return JSONResponse({"ok": True, "model": MODEL, "device": DEVICE, "sample_rate": SR})


app = Starlette(routes=[
    Route("/synthesize", synthesize, methods=["POST"]),
    Route("/health", health, methods=["GET"]),
])

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0",
                port=int(os.environ.get("BLAZEN_XTTS_PORT", "8091")), log_level="warning")
