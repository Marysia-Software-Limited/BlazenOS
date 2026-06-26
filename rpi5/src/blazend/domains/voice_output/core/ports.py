"""Ports for the voice-output domain.

TTS synthesis and PCM playback are Rust units (``blazend-tts``, ``blazend-audio-out``,
``blazend-player``); their contract is the IPC bus (``tts.frame``) and the ALSA
device — see docs/19-DOMAIN-ARCHITECTURE.md.

The Python-level seam is **playback arbitration**: on a single-channel HAT the
speaker is handed between spoken replies (audio-out) and a radio/stream
(``blazend-player``). ``PlaybackControlPort`` is that surface; today's
``orchestrator.radio_control.RadioControl`` satisfies it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

__all__ = ["PlaybackControlPort"]


class PlaybackControlPort(Protocol):
    """Own at most one stream and the speaker hand-off to/from spoken replies."""

    @property
    def playing(self) -> bool: ...

    def play(self, url: str, name: str = "") -> None: ...

    def stop(self) -> None: ...


if TYPE_CHECKING:
    # Static conformance: the rpi5 radio controller must satisfy the port.
    from blazend.orchestrator.radio_control import RadioControl

    def _rpi5_playback_conforms(ctl: RadioControl) -> PlaybackControlPort:
        return ctl
