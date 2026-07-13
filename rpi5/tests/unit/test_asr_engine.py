"""Unit tests for the Polish-first ASR engine (`blazend.domains.voice_input.adapters.rpi5.asr.engine`).

No model is loaded — a fake `WhisperBackend` is injected, so these run offline
in `make test-fast`. PL + EN parity per CLAUDE.md §8.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from blazend.domains.voice_input.adapters.rpi5.asr.engine import (
    BackendResult,
    Transcriber,
    coerce_language,
    confidence_from_logprob,
)

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _config_root(monkeypatch):
    monkeypatch.setenv("BLAZEN_CONFIG_ROOT", str(REPO / "configs"))


class FakeBackend:
    """Returns a canned BackendResult, ignoring the audio + forced language."""

    def __init__(self, language: str, text: str, avg_logprob: float = -0.2) -> None:
        self._result = BackendResult(language=language, text=text, avg_logprob=avg_logprob)

    def run(self, pcm, language):
        return self._result


def test_coerce_language_is_polish_first():
    assert coerce_language("pl") == "pl"
    assert coerce_language("en") == "en"
    assert coerce_language("en-US") == "en"
    assert coerce_language("de") == "pl"  # unsupported → Polish-first default
    assert coerce_language("") == "pl"


def test_confidence_from_logprob_bounds_and_monotonic():
    assert confidence_from_logprob(0.0) == 1.0
    assert 0.0 <= confidence_from_logprob(-20.0) <= 1.0
    assert confidence_from_logprob(-0.2) > confidence_from_logprob(-1.5)


def test_transcribe_polish():
    t = Transcriber(backend=FakeBackend("pl", "  która godzina  "))
    out = t.transcribe(np.zeros(1600, dtype=np.int16))
    assert out.language == "pl"
    assert out.text == "która godzina"
    assert 0.0 < out.confidence <= 1.0


def test_transcribe_english():
    t = Transcriber(backend=FakeBackend("en", "what time is it"))
    out = t.transcribe(np.zeros(1600, dtype=np.int16))
    assert out.language == "en"
    assert out.text == "what time is it"


def test_transcribe_coerces_unsupported_to_polish():
    t = Transcriber(backend=FakeBackend("de", "guten morgen"))
    out = t.transcribe(np.zeros(1600, dtype=np.int16))
    assert out.language == "pl"  # Polish-first


# -- false-wake harvest (`asr.__main__._save_false_wake`) ---------------------


def test_save_false_wake_writes_wav_and_prunes(tmp_path: Path) -> None:
    """A failed capture window is persisted as a mono 16 kHz wav tagged with WHY
    it failed, and the directory is pruned to the newest `_HARVEST_KEEP` clips —
    the raw material for `train-wake.py --neg-dir` retrains."""
    import wave

    from blazend.domains.voice_input.adapters.rpi5.asr import __main__ as asr_main

    pcm = [int(1000 * np.sin(i / 10)) for i in range(16_000)]  # 1 s of tone
    saved = asr_main._save_false_wake(pcm, 16_000, "empty", root=tmp_path)
    assert saved is not None and saved.exists()
    assert saved.name.endswith("_empty.wav")
    with wave.open(str(saved)) as w:
        assert w.getnchannels() == 1
        assert w.getframerate() == 16_000
        assert w.getnframes() == 16_000

    # Prune: fill past the cap, then one more save keeps only the newest KEEP.
    for i in range(asr_main._HARVEST_KEEP + 5):
        (tmp_path / f"20260101-{i:06d}_empty.wav").write_bytes(b"RIFF")
    asr_main._save_false_wake(pcm, 16_000, "notext", root=tmp_path)
    assert len(list(tmp_path.glob("*.wav"))) == asr_main._HARVEST_KEEP


def test_save_false_wake_survives_unwritable_root(tmp_path: Path) -> None:
    """Harvesting is best-effort: an unwritable target must not raise (the voice
    path always wins over the training-data side quest)."""
    from blazend.domains.voice_input.adapters.rpi5.asr import __main__ as asr_main

    blocked = tmp_path / "file-not-dir"
    blocked.write_text("occupies the path")  # mkdir(parents) under a FILE → OSError
    assert asr_main._save_false_wake([0] * 100, 16_000, "empty",
                                     root=blocked / "sub") is None
