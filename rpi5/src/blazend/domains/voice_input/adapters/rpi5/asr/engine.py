"""ASR engine — faster-whisper wrapper with **Polish-first** language handling.

The appliance speaks Polish first, English co-equal (see `docs/13-LANGUAGES.md`);
the `asr.final` wire enum is exactly ``{"pl", "en"}``. This module turns a slice
of mono 16 kHz PCM into a `Transcript`, forcing the detected language into that
enum with Polish as the tie-break/default.

`faster_whisper` is imported lazily (only when a real backend is built) so unit
tests and `make test-fast` stay offline and fast — tests inject a fake
`WhisperBackend`. Model selection comes from `configs/asr.yaml` (`active`,
overridable by ``BLAZEN_ASR_MODEL``; this 8 GB Pi defaults to `small`).
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Protocol

import numpy as np
import numpy.typing as npt

from blazend.config import load

# The `asr.final` language enum. Polish leads (Polish-first).
SUPPORTED_LANGUAGES = ("pl", "en")


@dataclass(frozen=True)
class Transcript:
    """One utterance result, shaped for the `asr.final` event."""

    language: str  # "pl" | "en"
    text: str
    confidence: float  # [0, 1]


@dataclass(frozen=True)
class BackendResult:
    """Raw output from a Whisper backend, before Polish-first coercion."""

    language: str  # detected or forced language code (may be e.g. "de")
    text: str
    avg_logprob: float  # mean per-segment avg_logprob (Whisper); ~0 is confident


class WhisperBackend(Protocol):
    """Minimal seam over faster-whisper so tests can inject a fake."""

    def run(self, pcm: npt.NDArray[np.float32], language: str | None) -> BackendResult: ...


def coerce_language(detected: str) -> str:
    """Force any detected language into the `{pl, en}` enum, **Polish-first**:
    English maps to ``en``; Polish and everything else (and the empty/ambiguous
    case) map to ``pl``."""
    code = (detected or "").lower()
    if code.startswith("en"):
        return "en"
    return "pl"


def confidence_from_logprob(avg_logprob: float) -> float:
    """Map a Whisper mean ``avg_logprob`` (log-probability, ≤ 0) to ``[0, 1]``."""
    return float(min(1.0, max(0.0, math.exp(avg_logprob))))


def _to_float32(pcm: npt.NDArray[np.int16] | npt.NDArray[np.float32]) -> npt.NDArray[np.float32]:
    if pcm.dtype == np.float32:
        return pcm.astype(np.float32, copy=False)
    return pcm.astype(np.float32) / 32768.0


def _resolve_model_dir(name: str) -> str:
    """Map an `asr.yaml` model name to a **baked local CT2 directory** when one
    exists, so faster-whisper loads it offline instead of trying to download it
    from HuggingFace. A bare name like ``small`` otherwise sends faster-whisper
    to the Hub — which on the appliance both has no network and, under the unit's
    ``ProtectHome=yes`` sandbox, fails reading ``~/.cache/huggingface/token``.

    Already-a-path names pass through. Search order: ``BLAZEN_MODELS_DIR`` (the
    repo convention), the appliance bake locations, then ``<cwd>/models``. Falls
    back to the bare name (dev machines with network) if nothing local matches.
    """
    if os.path.isdir(name):
        return name
    roots: list[str] = []
    env = os.environ.get("BLAZEN_MODELS_DIR")
    if env:
        roots.append(env)
    roots += [
        "/var/lib/blazen/models",
        "/usr/lib/blazen/models",
        os.path.join(os.getcwd(), "models"),
    ]
    for root in roots:
        candidate = os.path.join(root, "asr", name)
        if os.path.isdir(candidate):
            return candidate
    return name


class _FasterWhisperBackend:
    """Real backend. Imports faster-whisper lazily (CPU, int8)."""

    def __init__(
        self, model: str, compute_type: str, beam_size: int, initial_prompt: str = ""
    ) -> None:
        from faster_whisper import WhisperModel

        self._model = WhisperModel(
            _resolve_model_dir(model), device="cpu", compute_type=compute_type
        )
        self._beam_size = beam_size
        # A short domain prompt biases the decoder toward the vocabulary the
        # assistant actually hears (her name + common commands), which sharply
        # cuts misreads like "która godzina" → "kto jedzie na" on the quiet HAT.
        self._initial_prompt = initial_prompt or None

    def run(self, pcm: npt.NDArray[np.float32], language: str | None) -> BackendResult:
        segments, info = self._model.transcribe(
            pcm,
            language=language,
            beam_size=self._beam_size,
            initial_prompt=self._initial_prompt,
            # Each utterance is an independent short command — don't carry text
            # across (avoids faster-whisper's repeat-hallucination on noise).
            condition_on_previous_text=False,
            temperature=0.0,
        )
        seg_list = list(segments)
        text = "".join(s.text for s in seg_list)
        if seg_list:
            avg = sum(float(s.avg_logprob) for s in seg_list) / len(seg_list)
        else:
            avg = -10.0  # no speech → ~0 confidence
        detected = language if language is not None else str(info.language)
        return BackendResult(language=str(detected), text=str(text), avg_logprob=float(avg))


class Transcriber:
    """Transcribe PCM → `Transcript`, Polish-first. Pass `backend` in tests."""

    def __init__(self, *, backend: WhisperBackend | None = None) -> None:
        cfg = load("asr")
        self.model: str = os.environ.get("BLAZEN_ASR_MODEL") or str(cfg.get("active", "small"))
        self.language_mode: str = str(cfg.get("language", "auto"))  # auto | pl | en
        self.compute_type: str = str(cfg.get("compute_type", "int8"))
        self.beam_size: int = int(cfg.get("beam_size", 1))
        self.initial_prompt: str = str(cfg.get("initial_prompt", ""))
        self._backend: WhisperBackend | None = backend

    def _ensure_backend(self) -> WhisperBackend:
        if self._backend is None:
            self._backend = _FasterWhisperBackend(
                self.model, self.compute_type, self.beam_size, self.initial_prompt
            )
        return self._backend

    def transcribe(
        self, pcm: npt.NDArray[np.int16] | npt.NDArray[np.float32], sample_rate: int = 16_000
    ) -> Transcript:
        """Transcribe one utterance. `language_mode` `auto` lets Whisper detect
        (then coerced Polish-first); `pl`/`en` force that language."""
        backend = self._ensure_backend()
        forced = None if self.language_mode == "auto" else self.language_mode
        result = backend.run(_to_float32(pcm), forced)
        return Transcript(
            language=coerce_language(result.language),
            text=result.text.strip(),
            confidence=confidence_from_logprob(result.avg_logprob),
        )
