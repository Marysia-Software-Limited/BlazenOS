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

from blazend.config import load as load_config

log = logging.getLogger("blazend.domains.systems.adapters.rpi5.orchestrator.radio")

_PLAYER = "/usr/lib/blazen/bin/blazend-player"
# Forced-48 kHz playback PCM from /etc/asound.conf, NOT raw plughw: the Jabra
# SPEAK 410 advertises 8/16/48 kHz playback but its DAC clock never leaves
# 48 kHz, so a 16 kHz source (voice memos are recorded at the ASR rate) sent
# through plughw plays 3x fast — plug trusts the advertised rate and skips
# resampling. jabra_out pins the slave rate to 48 kHz so plug always resamples.
_DEVICE = "jabra_out"


def _level_flags() -> list[str]:
    """blazend-player loudness-leveler flags from ``audio.yaml`` (always applied so
    every source — radio, music, book — holds a constant real level). ``=`` form so
    clap doesn't read a negative dBFS value as another flag."""
    try:
        cfg = load_config("audio")
    except Exception:  # noqa: BLE001 — config missing → the player's own defaults are fine
        return []
    if not bool(cfg.get("leveling.enabled", True)):
        return ["--no-level"]
    return [
        f"--target-db={float(cfg.get('leveling.target_dbfs', -16.0))}",
        f"--max-boost-db={float(cfg.get('leveling.max_boost_db', 20.0))}",
        f"--limit-db={float(cfg.get('leveling.limit_dbfs', -1.0))}",
    ]


def _compress_flags() -> list[str]:
    """Speech-compressor flags — applied ONLY for spoken-word playback (books/
    podcasts), never music/radio. Empty when compression is disabled in config."""
    try:
        cfg = load_config("audio")
    except Exception:  # noqa: BLE001
        return ["--compress"]
    if not bool(cfg.get("compression.enabled", True)):
        return []
    return [
        "--compress",
        f"--comp-threshold-db={float(cfg.get('compression.threshold_dbfs', -24.0))}",
        f"--comp-ratio={float(cfg.get('compression.ratio', 3.0))}",
        f"--comp-makeup-db={float(cfg.get('compression.makeup_db', 3.0))}",
    ]


class RadioControl:
    """Owns at most one ``blazend-player`` stream.

    All methods are blocking (subprocess); call them off the event loop with
    ``asyncio.to_thread``.
    """

    def __init__(self) -> None:
        self._proc: subprocess.Popen[bytes] | None = None
        # Resolve the DSP flags once (config is static for the process lifetime).
        self._level = _level_flags()
        self._compress = _compress_flags()

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
             start_seconds: float = 0.0, speech: bool = False) -> None:
        """Start a stream: kill any current one, spawn the player. The orchestrator
        frees the Jabra output (stops audio-out via its half-duplex reconciler)
        before calling this, so the player can hold the speaker exclusively.

        For audiobooks the orchestrator passes ``position_file`` (the player writes
        its live position there for resume/attention) and ``start_seconds`` (seek
        into a chapter on resume). ``speech=True`` (books/podcasts) additionally
        enables the intelligibility compressor; the loudness leveler is always on."""
        self._kill()
        if not url:
            return
        try:
            # Let the player's warnings/errors reach the orchestrator journal
            # (DEVNULL previously hid a silent ALSA-busy death → "no reaction").
            env = {**os.environ, "RUST_LOG": os.environ.get("RUST_LOG", "warn,symphonia=error")}
            cmd = [_PLAYER, "--source", url, "--device", _DEVICE, *self._level]
            if speech:
                cmd += self._compress
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
