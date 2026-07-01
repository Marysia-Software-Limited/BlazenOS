"""Remote GPU whisper server for blazend-asr.

Runs on the dev host (paul, RTX 3090 — the same box that serves Bielik via
Ollama at :11434). blazend-asr on the Pi POSTs raw 16 kHz mono i16 PCM here and
gets back the transcript, transcribed with a big multilingual model on the GPU —
accurate AND fast, unlike `medium` on the Pi CPU (~13x realtime). This keeps ML
off the Pi only for the dev/far-field path; the appliance default stays local.

Run:  BLAZEN_WHISPER_MODEL=<ct2-dir-or-name> .venv-whisper/bin/python \
        scripts/whisper_server.py   # listens on 0.0.0.0:8090
POST: /transcribe?sr=16000&lang=pl   body = raw i16 PCM   -> {text, language, avg_logprob}
"""

from __future__ import annotations

import glob
import os

# Point ctranslate2 at the pip-installed cuDNN/cuBLAS before importing it.
_here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_libs = ":".join(glob.glob(os.path.join(_here, ".venv-whisper/lib/python*/site-packages/nvidia/*/lib")))
os.environ["LD_LIBRARY_PATH"] = _libs + ":" + os.environ.get("LD_LIBRARY_PATH", "")

import numpy as np  # noqa: E402
import uvicorn  # noqa: E402
from faster_whisper import WhisperModel  # noqa: E402
from starlette.applications import Starlette  # noqa: E402
from starlette.requests import Request  # noqa: E402
from starlette.responses import JSONResponse  # noqa: E402
from starlette.routing import Route  # noqa: E402

MODEL = os.environ.get("BLAZEN_WHISPER_MODEL", "large-v3")
DEVICE = os.environ.get("BLAZEN_WHISPER_DEVICE", "cuda")
COMPUTE = os.environ.get("BLAZEN_WHISPER_COMPUTE", "float16")
print(f"loading whisper {MODEL} on {DEVICE} ({COMPUTE})…", flush=True)
model = WhisperModel(MODEL, device=DEVICE, compute_type=COMPUTE)
print("ready", flush=True)


async def transcribe(request: Request) -> JSONResponse:
    sr = int(request.query_params.get("sr", "16000"))
    lang = request.query_params.get("lang", "pl")
    raw = await request.body()
    pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if sr != 16000 and pcm.size > 1:  # whisper wants 16 kHz
        n = int(pcm.size * 16000 / sr)
        pcm = np.interp(np.linspace(0, pcm.size, n, endpoint=False), np.arange(pcm.size), pcm).astype(np.float32)
    peak = float(np.max(np.abs(pcm))) if pcm.size else 0.0
    if peak > 0.01:  # peak-normalize quiet far-field audio
        pcm = pcm * (0.95 / peak)
    segs, _info = model.transcribe(
        pcm, language=(None if lang == "auto" else lang), beam_size=5,
        temperature=0.0, condition_on_previous_text=False, vad_filter=True,
        no_speech_threshold=0.6,
    )
    seg_list = list(segs)
    text = "".join(s.text for s in seg_list).strip()
    avg = (sum(float(s.avg_logprob) for s in seg_list) / len(seg_list)) if seg_list else -10.0
    return JSONResponse({"text": text, "language": lang, "avg_logprob": avg})


async def health(_request: Request) -> JSONResponse:
    return JSONResponse({"ok": True, "model": MODEL, "device": DEVICE})


app = Starlette(routes=[
    Route("/transcribe", transcribe, methods=["POST"]),
    Route("/health", health, methods=["GET"]),
])

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("BLAZEN_WHISPER_PORT", "8090")), log_level="warning")
