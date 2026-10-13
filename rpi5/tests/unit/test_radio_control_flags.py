"""blazend-player DSP flags built by RadioControl from ``audio.yaml``.

Spoken-word playback (books/podcasts) must level toward the louder
``leveling.speech_target_dbfs`` while music/radio keep ``target_dbfs``
(user request 2026-10-11: audiobooks were barely audible)."""
from __future__ import annotations

from blazend.domains.voice_output.adapters.rpi5 import radio_control as rc


def _target(flags: list[str]) -> float:
    return float(next(f for f in flags if f.startswith("--target-db=")).split("=", 1)[1])


def test_music_levels_toward_default_target():
    assert _target(rc._level_flags()) == -16.0


def test_speech_levels_louder_than_music():
    speech = _target(rc._level_flags(speech=True))
    assert speech == -12.0
    assert speech > _target(rc._level_flags())


def test_speech_keeps_the_limiter_ceiling():
    flags = rc._level_flags(speech=True)
    assert "--limit-db=-1.0" in flags


def test_speech_also_enables_the_compressor():
    assert "--compress" in rc._compress_flags()
