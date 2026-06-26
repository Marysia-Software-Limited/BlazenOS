"""Radio/stream playback for the orchestrator.

The ReSpeaker HAT has a single playback channel, so the TTS service
(``blazend-audio-out``) and the stream player (``blazend-player``) cannot both
hold it. So while a stream plays we stop audio-out (freeing the speaker) and
restart it when the stream stops (so replies can be spoken). Stopping/starting
audio-out is allowed by a narrow sudoers rule (``/etc/sudoers.d/blazen-audio-out``);
the orchestrator unit drops ``NoNewPrivileges``/``RestrictSUIDSGID`` so sudo runs.
"""

from __future__ import annotations

import logging
import subprocess

log = logging.getLogger("blazend.orchestrator.radio")

_PLAYER = "/usr/lib/blazen/bin/blazend-player"
_DEVICE = "plughw:CARD=wm8960soundcard,DEV=0"


class RadioControl:
    """Owns at most one ``blazend-player`` stream + the audio-out hand-off.

    All methods are blocking (subprocess + systemctl); call them off the event
    loop with ``asyncio.to_thread``.
    """

    def __init__(self) -> None:
        self._proc: subprocess.Popen[bytes] | None = None

    @property
    def playing(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _audio_out(self, action: str) -> None:
        try:
            subprocess.run(
                ["sudo", "-n", "systemctl", action, "blazend-audio-out"],
                check=False,
                timeout=10,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("systemctl %s blazend-audio-out failed: %s", action, exc)

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
        """Start a stream: kill any current one, free the HAT, spawn the player."""
        self._kill()
        if not url:
            return
        self._audio_out("stop")  # release the speaker for the stream
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
            self._audio_out("start")  # nothing playing → give TTS the speaker back

    def stop(self) -> None:
        """Stop the stream (if any) and return the HAT to TTS (audio-out)."""
        was_playing = self.playing
        self._kill()
        if was_playing:
            log.info("radio stopped")
        self._audio_out("start")
