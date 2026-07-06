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
    return Response(_wav_bytes(np.asarray(wav), SR), media_type="audio/wav")


async def health(_request: Request) -> JSONResponse:
    return JSONResponse({"ok": True, "model": MODEL, "device": DEVICE, "sample_rate": SR})


app = Starlette(routes=[
    Route("/synthesize", synthesize, methods=["POST"]),
    Route("/health", health, methods=["GET"]),
])

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0",
                port=int(os.environ.get("BLAZEN_XTTS_PORT", "8091")), log_level="warning")
