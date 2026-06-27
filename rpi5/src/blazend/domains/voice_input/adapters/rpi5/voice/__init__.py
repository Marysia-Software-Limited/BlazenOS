"""Hands-free voice loop (S5) — the single-process "Hej Jessico" runner.

Unlike the split appliance pipeline (`blazend-asr` + `blazend-brain` +
`blazend-tts` as separate units), the voice runner owns ASR → engine → TTS in
one Python process that reads the Rust audio ring directly. This is the path
the live Pi-5 rig uses (`scripts/voice-run.sh`): only the two Rust units that
own the mic (`blazend-audio-in`, `blazend-wake`) run alongside it, so there is
no ALSA device contention. See :mod:`blazend.domains.voice_input.adapters.rpi5.voice.runner`.
"""

from __future__ import annotations

from blazend.domains.voice_input.adapters.rpi5.voice.runner import VoiceRunner, build_runner

__all__ = ["VoiceRunner", "build_runner"]
