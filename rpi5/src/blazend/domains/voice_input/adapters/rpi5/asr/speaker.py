"""Speaker identification — is the utterance the OWNER's voice? (2026-09-08)

A WeSpeaker ResNet34 (VoxCeleb) ONNX embedder (256-d) over kaldi-style
80-bin log-mel fbank computed here in numpy (the runtime venv is py3.14:
onnxruntime only, no torch — same constraint as the wake chain). Speaker
identity is language-independent enough that the VoxCeleb model works for
Polish; the threshold is calibrated per-site from the owner's own
voice-training recordings vs the harvested false-wake clips
(scripts/speaker-calibrate.py).

The profile is a list of enrollment embeddings (voice training samples +
confirmed-command clips, capped) stored in ``<data>/speaker/profile.json``.
Similarity of an utterance = max cosine against the enrollment set (max, not
mean-of-centroid: the owner sounds different across rooms/distances and any
one close match is evidence enough).

Config: ``configs/speaker-id.yaml`` — model declaration (install_models.py),
``threshold`` and ``enforce``. Everything degrades soft: no model file or no
profile → ``available`` is False and the caller must treat every utterance
as the owner (exactly the pre-feature behaviour).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import numpy as np

log = logging.getLogger(
    "blazend.domains.voice_input.adapters.rpi5.asr.speaker")

_SR = 16_000
_NFFT = 512
_FRAME = 400  # 25 ms
_HOP = 160  # 10 ms
_NMELS = 80


def _models_root() -> Path:
    root = os.environ.get("BLAZEN_MODELS_DIR")
    if root:
        return Path(root)
    data = os.environ.get("BLAZEN_DATA_DIR")
    if data:  # desktop layout: ~/.local/share/blazen/{data,models}
        return Path(data).parent / "models"
    return Path("/var/lib/blazen/models")


def _data_root() -> Path:
    data = os.environ.get("BLAZEN_DATA_DIR")
    return Path(data) if data else Path("/var/lib/blazen/data")


def _mel_filterbank() -> np.ndarray:
    """Kaldi-compatible mel filterbank (80 bins, 20 Hz..Nyquist)."""
    def mel(f: np.ndarray | float) -> np.ndarray | float:
        return 1127.0 * np.log(1.0 + np.asarray(f) / 700.0)

    low, high = mel(20.0), mel(_SR / 2.0)
    points = np.linspace(low, high, _NMELS + 2)
    bins = 700.0 * (np.exp(points / 1127.0) - 1.0)
    fft_freqs = np.arange(_NFFT // 2 + 1) * _SR / _NFFT
    fb = np.zeros((_NMELS, _NFFT // 2 + 1), dtype=np.float32)
    for m in range(_NMELS):
        left, center, right = bins[m], bins[m + 1], bins[m + 2]
        up = (fft_freqs - left) / max(center - left, 1e-9)
        down = (right - fft_freqs) / max(right - center, 1e-9)
        fb[m] = np.maximum(0.0, np.minimum(up, down))
    return fb


_FB = _mel_filterbank()
# Povey window (kaldi default): hann^0.85.
_WINDOW = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(_FRAME) / (_FRAME - 1))) ** 0.85


def fbank(pcm_i16: np.ndarray) -> np.ndarray:
    """Kaldi-style log-mel fbank ``(T, 80)`` of mono 16 kHz i16 audio, with
    per-utterance mean subtraction (CMN) — WeSpeaker's expected input
    (``normalize_samples: 0`` → int16-scale floats)."""
    x = np.asarray(pcm_i16, dtype=np.float32)
    n = 1 + max(0, (len(x) - _FRAME)) // _HOP
    if n < 5:
        return np.zeros((0, _NMELS), dtype=np.float32)
    idx = np.arange(_FRAME)[None, :] + _HOP * np.arange(n)[:, None]
    frames = x[idx]
    frames = frames - frames.mean(axis=1, keepdims=True)  # kaldi remove_dc
    frames = np.concatenate(  # pre-emphasis 0.97 within the frame
        [frames[:, :1] * (1 - 0.97), frames[:, 1:] - 0.97 * frames[:, :-1]],
        axis=1)
    spec = np.abs(np.fft.rfft(frames * _WINDOW, n=_NFFT)) ** 2
    mel = np.log(np.maximum(spec @ _FB.T, 1.19e-7))
    out: np.ndarray = (mel - mel.mean(axis=0, keepdims=True)).astype(np.float32)
    return out


def speech_bursts(pcm_i16: np.ndarray, *, floor: float = 300.0,
                  min_s: float = 0.6, pad_s: float = 0.2,
                  ) -> list[np.ndarray]:
    """Split a recording-session wav into individual speech bursts (simple
    200 ms RMS gating). Enrollment embeds each utterance separately — one
    embedding per 17 s session (utterances + silence + prompts leaking in)
    scored visibly worse than per-burst embeddings (calibration 2026-09-08)."""
    x = np.asarray(pcm_i16, dtype=np.float64)
    win = _SR // 5
    n = len(x) // win
    if n < 2:
        return [np.asarray(pcm_i16, dtype=np.int16)]
    rms = np.sqrt((x[: n * win].reshape(n, win) ** 2).mean(axis=1))
    hot = rms >= floor
    bursts: list[np.ndarray] = []
    start = None
    for i, h in enumerate([*hot, False]):
        if h and start is None:
            start = i
        elif not h and start is not None:
            b0 = max(0, int((start - pad_s * 5) * win))
            b1 = min(len(x), int((i + pad_s * 5) * win))
            if (b1 - b0) / _SR >= min_s:
                bursts.append(np.asarray(pcm_i16[b0:b1], dtype=np.int16))
            start = None
    return bursts or [np.asarray(pcm_i16, dtype=np.int16)]


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(a @ b / denom) if denom > 0 else 0.0


class SpeakerId:
    """Embedder + owner profile. Soft-degrading: ``available`` is False when
    the model or the profile is missing and callers fall back to trusting
    every voice (pre-feature behaviour)."""

    MODEL_FILE = "speaker/wespeaker_en_voxceleb_resnet34.onnx"
    PROFILE_CAP = 50

    def __init__(self, model_path: Path | None = None,
                 profile_path: Path | None = None) -> None:
        self._model_path = model_path or (_models_root() / self.MODEL_FILE)
        self._profile_path = profile_path or (
            _data_root() / "speaker" / "profile.json")
        self._session = None
        self._profile: np.ndarray | None = None  # (N, 256)
        self._profile_mtime = 0.0

    @property
    def available(self) -> bool:
        return self._ensure_session() is not None and self._load_profile() is not None

    def _ensure_session(self) -> object | None:
        if self._session is None and self._model_path.is_file():
            try:
                import onnxruntime as ort

                opts = ort.SessionOptions()
                opts.intra_op_num_threads = 2  # same cap rationale as wake
                self._session = ort.InferenceSession(
                    str(self._model_path), opts)
                log.info("speaker model loaded: %s", self._model_path.name)
            except Exception as exc:  # noqa: BLE001 — degrade, never break ASR
                log.warning("speaker model unusable: %s", exc)
        return self._session

    def embed(self, pcm_i16: np.ndarray) -> np.ndarray | None:
        """256-d embedding of an utterance (None if too short / no model)."""
        sess = self._ensure_session()
        if sess is None:
            return None
        feats = fbank(pcm_i16)
        if feats.shape[0] < 25:  # < 0.25 s of frames — nothing to identify
            return None
        out = sess.run(None, {"feats": feats[None, ...]})[0][0]  # type: ignore[attr-defined]
        return np.asarray(out, dtype=np.float32)

    def _load_profile(self) -> np.ndarray | None:
        try:
            mtime = self._profile_path.stat().st_mtime
        except OSError:
            return None
        if self._profile is None or mtime != self._profile_mtime:
            try:
                raw = json.loads(self._profile_path.read_text(encoding="utf-8"))
                embs = np.asarray(raw.get("embeddings", []), dtype=np.float32)
                self._profile = embs if embs.size else None
                self._profile_mtime = mtime
                if self._profile is not None:
                    log.info("speaker profile: %d enrollment embeddings",
                             len(self._profile))
            except (OSError, ValueError) as exc:
                log.warning("speaker profile unreadable: %s", exc)
                return None
        return self._profile

    def similarity(self, pcm_i16: np.ndarray) -> float | None:
        """Max cosine of the utterance vs the enrollment set (None = cannot
        tell: no model / no profile / too-short audio → trust the voice)."""
        profile = self._load_profile()
        if profile is None:
            return None
        emb = self.embed(pcm_i16)
        if emb is None:
            return None
        return max(cosine(emb, p) for p in profile)

    # -- enrollment ----------------------------------------------------
    def enroll_dir(self, wav_dir: Path, extra: list[np.ndarray] | None = None,
                   ) -> int:
        """(Re)build the profile from every wav in ``wav_dir`` (the voice
        training samples) plus optional extra embeddings (confirmed command
        clips). Returns the enrollment count (0 = nothing written)."""
        import wave

        embs: list[list[float]] = []
        for p in sorted(wav_dir.glob("*.wav")):
            try:
                with wave.open(str(p)) as w:
                    pcm = np.frombuffer(
                        w.readframes(w.getnframes()), dtype=np.int16)
            except (OSError, wave.Error) as exc:
                log.debug("enroll skip %s: %s", p.name, exc)
                continue
            for burst in speech_bursts(pcm):
                e = self.embed(burst)
                if e is not None:
                    embs.append([float(v) for v in e])
        for e in extra or []:
            embs.append([float(v) for v in np.asarray(e, dtype=np.float32)])
        embs = embs[-self.PROFILE_CAP:]
        if not embs:
            return 0
        self._profile_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._profile_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"embeddings": embs}), encoding="utf-8")
        tmp.replace(self._profile_path)
        self._profile = None  # force reload
        return len(embs)
