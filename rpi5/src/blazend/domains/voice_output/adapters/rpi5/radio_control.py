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
import os
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

    def play(self, url: str, name: str = "", *, position_file: str = "",
             start_seconds: float = 0.0) -> None:
        """Start a stream: kill any current one, spawn the player. The orchestrator
        frees the Jabra output (stops audio-out via its half-duplex reconciler)
        before calling this, so the player can hold the speaker exclusively.

        For audiobooks the orchestrator passes ``position_file`` (the player writes
        its live position there for resume/attention) and ``start_seconds`` (seek
        into a chapter on resume)."""
        self._kill()
        if not url:
            return
        try:
            # Let the player's warnings/errors reach the orchestrator journal
            # (DEVNULL previously hid a silent ALSA-busy death → "no reaction").
            env = {**os.environ, "RUST_LOG": os.environ.get("RUST_LOG", "warn,symphonia=error")}
            cmd = [_PLAYER, "--source", url, "--device", _DEVICE]
            if position_file:
                cmd += ["--position-file", position_file]
            if start_seconds > 0:
                cmd += ["--start-seconds", str(start_seconds)]
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=None, env=env,
            )
            log.info("radio play %s%s", name or url,
                     f" (from {start_seconds:.0f}s)" if start_seconds > 0 else "")
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
