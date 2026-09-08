"""Tier 0 — speaker identification (2026-09-08): fbank/cosine math, the
profile lifecycle, soft degradation, burst splitting and the orchestrator's
foreign-voice policy. Offline: no ONNX model — availability degrades and the
embedding path is faked where needed."""

from __future__ import annotations

import asyncio
import types

import numpy as np
import pytest

from blazend.domains.voice_input.adapters.rpi5.asr.__main__ import _speaker_fields
from blazend.domains.voice_input.adapters.rpi5.asr.speaker import (
    SpeakerId,
    cosine,
    fbank,
    speech_bursts,
)

SR = 16_000


def test_fbank_shape_and_cmn():
    rng = np.random.default_rng(3)
    pcm = (rng.normal(0, 2000, SR * 2)).astype(np.int16)
    f = fbank(pcm)
    assert f.shape[1] == 80 and f.shape[0] > 150  # ~10 ms hop over 2 s
    assert abs(float(f.mean(axis=0).max())) < 1e-4  # per-utterance CMN applied
    assert fbank(np.zeros(100, dtype=np.int16)).shape == (0, 80)  # too short


def test_cosine_basics():
    a = np.array([1.0, 0.0], dtype=np.float32)
    assert cosine(a, a) == pytest.approx(1.0)
    assert cosine(a, np.array([0.0, 1.0], dtype=np.float32)) == pytest.approx(0.0)
    assert cosine(a, np.zeros(2, dtype=np.float32)) == 0.0


def test_speech_bursts_split_a_session():
    rng = np.random.default_rng(5)
    speech = (rng.normal(0, 3000, SR)).astype(np.int16)
    gap = np.zeros(SR, dtype=np.int16)
    session = np.concatenate([gap, speech, gap, speech, gap])
    bursts = speech_bursts(session)
    assert len(bursts) == 2
    assert all(len(b) >= int(0.6 * SR) for b in bursts)


def test_missing_model_degrades_soft(tmp_path):
    sid = SpeakerId(model_path=tmp_path / "nope.onnx",
                    profile_path=tmp_path / "profile.json")
    assert not sid.available
    assert sid.embed(np.zeros(SR, dtype=np.int16)) is None
    assert sid.similarity(np.zeros(SR, dtype=np.int16)) is None
    assert sid.enroll_dir(tmp_path) == 0


def test_similarity_uses_max_over_profile(tmp_path):
    import json

    sid = SpeakerId(model_path=tmp_path / "nope.onnx",
                    profile_path=tmp_path / "profile.json")
    (tmp_path / "profile.json").write_text(json.dumps(
        {"embeddings": [[1.0, 0.0], [0.0, 1.0]]}))
    sid.embed = lambda pcm: np.array([0.0, 1.0], dtype=np.float32)  # type: ignore[method-assign]
    assert sid.similarity(np.zeros(SR, dtype=np.int16)) == pytest.approx(1.0)


def test_speaker_fields_shape():
    class _Sid:
        def similarity(self, pcm):
            return 0.7123

    f = _speaker_fields(_Sid(), np.zeros(SR, dtype=np.int16), 0.45)
    assert f == {"speaker_sim": 0.712, "speaker_owner": True}
    f2 = _speaker_fields(_Sid(), np.zeros(SR, dtype=np.int16), 0.8)
    assert f2["speaker_owner"] is False

    class _NoTell:
        def similarity(self, pcm):
            return None

    assert _speaker_fields(_NoTell(), np.zeros(SR, dtype=np.int16), 0.45) == {}
    assert _speaker_fields(None, np.zeros(SR, dtype=np.int16), 0.45) == {}


# ----- orchestrator foreign-voice policy --------------------------------------

from blazend.domains.systems.adapters.rpi5.orchestrator.supervisor import (  # noqa: E402
    Orchestrator,
)


def _stub(enforce: bool) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        _speaker_enforce=enforce,
        _speaker_allow_any=frozenset({"stop_talking", "volume_up"}),
    )


def test_foreign_voice_policy():
    block = Orchestrator._foreign_voice_block
    on = _stub(enforce=True)
    # Foreign voice + guarded intent → blocked.
    assert block(on, "radio_play", {"speaker_owner": False, "speaker_sim": 0.2})
    # Safety intents are never blocked, whoever speaks.
    assert not block(on, "stop_talking", {"speaker_owner": False})
    assert not block(on, "volume_up", {"speaker_owner": False})
    # Owner and unknown voices always pass.
    assert not block(on, "radio_play", {"speaker_owner": True, "speaker_sim": 0.8})
    assert not block(on, "radio_play", {})  # no fields = cannot tell = trust
    # Log-only mode never blocks.
    off = _stub(enforce=False)
    assert not block(off, "radio_play", {"speaker_owner": False, "speaker_sim": 0.2})


def test_foreign_blip_rate_limited(tmp_path):
    played = []

    async def fake_beep(wav):
        played.append(wav)

    stub = types.SimpleNamespace(
        _earcons={"foreign_voice": True},
        _foreign_cue_at=0.0,
        _foreign_blip_wav=b"blip",
        _play_beep=fake_beep,
    )

    async def run() -> None:
        await Orchestrator._notify_foreign_voice(stub)
        await Orchestrator._notify_foreign_voice(stub)  # inside cooldown

    asyncio.run(run())
    assert played == [b"blip"]
