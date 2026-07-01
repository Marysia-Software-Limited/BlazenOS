"""Radio/stream playback for the orchestrator.

Playback goes to the **Jabra SPEAK 410** USB speakerphone (card id ``USB``), the
appliance's speaker now that the ReSpeaker HAT is removed. A single ALSA output
PCM can't be held by both the TTS service (``blazend-audio-out``) and the stream
player (``blazend-player``) at once. The orchestrator's half-duplex reconciler
(see ``supervisor.py``) owns audio-out — it is already DOWN while listening and
is kept down while a stream plays — so this class only manages the player
process. Stopping/starting audio-out (done by the supervisor) is allowed by a
narrow sudoers rule (``/etc/sudoers.d/blazen-audio-out``).
"""

from __future__ import annotations

import logging
import subprocess

log = logging.getLogger("blazend.domains.systems.adapters.rpi5.orchestrator.radio")

_PLAYER = "/usr/lib/blazen/bin/blazend-player"
# Jabra SPEAK 410 USB out (48 kHz stereo native; blazend-player resamples to it).
_DEVICE = "plughw:CARD=USB,DEV=0"


class RadioControl:
    """Owns at most one ``blazend-player`` stream.

    All methods are blocking (subprocess); call them off the event loop with
    ``asyncio.to_thread``.
    """

    def __init__(self) -> None:
        self._proc: subprocess.Popen[bytes] | None = None

    @property
    def playing(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _kill(self) -> None:
        if self._proc is None:
            return
        try:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        except OSError:
            pass
        self._proc = None

    def play(self, url: str, name: str = "") -> None:
        """Start a stream: kill any current one, spawn the player. The orchestrator
        frees the Jabra output (stops audio-out via its half-duplex reconciler)
        before calling this, so the player can hold the speaker exclusively."""
        self._kill()
        if not url:
            return
        try:
            self._proc = subprocess.Popen(
                [_PLAYER, "--source", url, "--device", _DEVICE],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            log.info("radio play %s", name or url)
        except OSError as exc:
            log.warning("radio play failed: %s", exc)
            self._proc = None

    def stop(self) -> None:
        """Stop the stream (if any). The orchestrator's reconciler restores
        audio-out to the (down-while-listening) half-duplex default."""
        was_playing = self.playing
        self._kill()
        if was_playing:
            log.info("radio stopped")
