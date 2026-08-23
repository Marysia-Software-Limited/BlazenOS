"""openWakeWord feature extraction for few-shot wake-word matching.

Runs the two baked openWakeWord ONNX models with only numpy + onnxruntime
(no scipy / no openwakeword package):

    raw int16 audio  --melspectrogram.onnx-->  mel [T, 32]
    mel window (76 frames)  --embedding_model.onnx-->  embedding [96]

The 96-d embedding represents ~0.76 s of audio (76 mel frames at a 10 ms hop).
Enrol a wake word by storing its embeddings (or its loudest ~0.7 s mel segment
for DTW), then match live audio against them — see
docs/findings/wake-word-wm8960.md for the recipe and why this is currently
shelved (WM8960 mic quality). Speaker-dependent and transcription-free, so it
should work on a clean mic where ASR-based detection also would.
"""

from __future__ import annotations

import os

import numpy as np
import numpy.typing as npt
import onnxruntime as ort

# BLAZEN_MODELS_DIR override (2026-08-23, desktop installs); Pi default unchanged.
_WAKE_DIR = (
    f"{os.environ['BLAZEN_MODELS_DIR']}/wake"
    if os.environ.get("BLAZEN_MODELS_DIR")
    else "/var/lib/blazen/models/wake"
)
#: mel-frame hop in audio samples (16 kHz → 10 ms frames).
HOP = 160
#: mel frames per embedding window.
WIN_FRAMES = 76

_mel_session: ort.InferenceSession | None = None
_emb_session: ort.InferenceSession | None = None


def _sessions() -> tuple[ort.InferenceSession, ort.InferenceSession]:
    global _mel_session, _emb_session
    if _mel_session is None or _emb_session is None:
        ort.set_default_logger_severity(3)
        _mel_session = ort.InferenceSession(f"{_WAKE_DIR}/melspectrogram.onnx")
        _emb_session = ort.InferenceSession(f"{_WAKE_DIR}/embedding_model.onnx")
    return _mel_session, _emb_session


def melspec(audio_i16: npt.ArrayLike) -> npt.NDArray[np.float32]:
    """Mel spectrogram ``[T, 32]`` from raw int16 audio (openWakeWord transform)."""
    mel_s, _ = _sessions()
    x = np.asarray(audio_i16, dtype=np.float32)[None, :]
    out: npt.NDArray[np.float32] = np.asarray(
        np.squeeze(mel_s.run(None, {"input": x})[0]), dtype=np.float32
    )  # [T, 32]
    if out.ndim == 1:
        out = out[None, :]
    return np.asarray(out / 10.0 + 2.0, dtype=np.float32)


def embeddings(audio_i16: npt.ArrayLike, step: int = 8) -> npt.NDArray[np.float32]:
    """Sliding-window embeddings ``[N, 96]`` over the audio (step in mel frames)."""
    _, emb_s = _sessions()
    mel = melspec(audio_i16)
    out: list[npt.NDArray[np.float32]] = []
    for i in range(0, max(1, mel.shape[0] - WIN_FRAMES + 1), step):
        win = mel[i : i + WIN_FRAMES]
        if win.shape[0] < WIN_FRAMES:
            break
        e = emb_s.run(None, {"input_1": win[None, :, :, None].astype(np.float32)})[0]
        out.append(np.squeeze(e))
    return np.asarray(out, dtype=np.float32) if out else np.zeros((0, 96), dtype=np.float32)
