"""Give a Linux node a voice: render Polish text via the mesh TTS and play it.

paul has the GPU **XTTS-v2** service (:8091) and an ALSA output, so it can both
render (its GPU) and speak. The TTS endpoint is resolved from the shared mesh
registry — no hardcoded URL. Playback reuses **blazend-player** with the speech
compressor + loudness leveler, exactly like the Pi appliance, so Jessica's voice
is consistent across nodes.

This is the render+play primitive; reading a whole Calibre/Wolne-Lektury book is
the ingest pipeline built on top of it.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mesh_registry import Mesh

_PLAYER_ENV = "BLAZEN_PLAYER_BIN"
_DEVICE_ENV = "BLAZEN_AUDIO_DEVICE"
# Native (host-arch) blazend-player build; overridable via $BLAZEN_PLAYER_BIN.
_REPO_PLAYER = Path(__file__).resolve().parents[4] / "rpi5/crates/target/release/blazend-player"


def _player_bin() -> str:
    return os.environ.get(_PLAYER_ENV) or str(_REPO_PLAYER)


def _device() -> str:
    return os.environ.get(_DEVICE_ENV, "default")


class Voice:
    """Render + play speech through a node's mesh TTS resource (XTTS on paul)."""

    def __init__(
        self,
        *,
        mesh: Mesh | None = None,
        tts_name: str = "xtts",
        opener: Callable[..., Any] = urllib.request.urlopen,
        runner: Callable[..., Any] = subprocess.run,
    ) -> None:
        mesh = mesh or Mesh.load()
        res = mesh.resource("tts", tts_name)
        self._url = res.url if res else None
        self._language = str((res.attrs.get("language") if res else None) or "pl")
        self._speaker = (res.attrs.get("speaker") if res else None) or None
        self._opener = opener  # injectable for tests (no network)
        self._runner = runner

    @property
    def available(self) -> bool:
        return bool(self._url)

    def render(self, text: str) -> bytes:
        """POST `text` to the mesh TTS endpoint → WAV bytes."""
        if not self._url:
            raise RuntimeError("no TTS resource for this node in the mesh")
        payload: dict[str, str] = {"text": text, "language": self._language}
        if self._speaker:
            payload["speaker"] = str(self._speaker)
        req = urllib.request.Request(  # noqa: S310 — our own LAN GPU service
            self._url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with self._opener(req, timeout=600) as resp:
            return bytes(resp.read())

    def play_wav(self, wav: bytes, *, device: str | None = None) -> None:
        """Play already-rendered WAV bytes via blazend-player (speech compressor on;
        leveling/AGC is on by default). Blocks until playback finishes."""
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(wav)
            path = f.name
        try:
            self._runner(
                [_player_bin(), "--source", path, "--device", device or _device(), "--compress"],
                check=False,
            )
        finally:
            Path(path).unlink(missing_ok=True)

    def play_file(self, source: str, *, device: str | None = None) -> None:
        """Play an existing audio file or URL via blazend-player (compressor on).
        Blocks until it finishes. Used for pre-rendered audiobook chapters."""
        self._runner(
            [_player_bin(), "--source", source, "--device", device or _device(), "--compress"],
            check=False,
        )

    def speak(self, text: str, *, device: str | None = None) -> None:
        """Render `text` and play it (render + play_wav)."""
        self.play_wav(self.render(text), device=device)
