"""Ports for the voice-input domain.

Capture, wake, and VAD are Rust units (``blazend-audio-in``, ``blazend-wake``)
whose contract with the rest of the system is the **IPC event bus** (``audio.frame``,
``wake.detected``, ``vad.start`` / ``vad.end``), not a Python call — see
``configs/_schema/events/`` and docs/19-DOMAIN-ARCHITECTURE.md.

The one Python-level seam is ASR: ``AsrBackendPort`` is the existing
``WhisperBackend`` Protocol (faster-whisper today; a GPU/Orin or OS-speech backend
later), injected into ``blazend.asr.engine.Transcriber``. The Transcriber's own
output crosses to the mind as the ``asr.final`` event.
"""

from __future__ import annotations

from blazend.asr.engine import WhisperBackend as AsrBackendPort

__all__ = ["AsrBackendPort"]
