"""tests/tools/audio_backend.py — host-side abstraction over the two
virtual-audio bridges used by the e2e runner.

Backends:
  - pipewire    : Linux dev hosts; null-sink + virtual-mic loopback.
  - portaudio-file : macOS hosts (and CI fallback); WAV file backing.

The contract is intentionally narrow: play_to_vm(wav) and capture_from_vm(timeout).
"""
from __future__ import annotations

import abc
import shutil
import subprocess
from pathlib import Path


class AudioBackend(abc.ABC):
    @abc.abstractmethod
    def play_to_vm(self, wav: Path) -> None: ...
    @abc.abstractmethod
    def capture_from_vm(self, out_wav: Path, timeout_ms: int) -> Path: ...
    @abc.abstractmethod
    def setup(self) -> None: ...
    @abc.abstractmethod
    def teardown(self) -> None: ...


class PipeWireBackend(AudioBackend):
    """Linux host. Uses `pw-cli` + `pw-loopback` to wire null sink ↔ VM."""

    def __init__(self, null_sink: str, capture_node: str):
        self.null_sink = null_sink
        self.capture_node = capture_node

    def setup(self) -> None:
        if shutil.which("pw-cli") is None:
            raise RuntimeError("PipeWire tools not on PATH")
        # TODO(M2): pw-cli create-node + pw-loopback wiring

    def teardown(self) -> None:
        # TODO(M2): pw-cli destroy-node
        pass

    def play_to_vm(self, wav: Path) -> None:
        subprocess.run(["pw-cat", "-p", str(wav)], check=True)

    def capture_from_vm(self, out_wav: Path, timeout_ms: int) -> Path:
        subprocess.run(
            ["pw-cat", "-r", str(out_wav), "--target", self.capture_node, "--timeout", str(timeout_ms / 1000)],
            check=True,
        )
        return out_wav


class PortAudioFileBackend(AudioBackend):
    """Cross-platform fallback. WAV files mediate host ↔ VM exchange."""

    def __init__(self, in_wav: Path, out_wav: Path):
        self.in_wav = in_wav
        self.out_wav = out_wav

    def setup(self) -> None:
        self.in_wav.parent.mkdir(parents=True, exist_ok=True)

    def teardown(self) -> None:
        pass

    def play_to_vm(self, wav: Path) -> None:
        shutil.copyfile(wav, self.in_wav)

    def capture_from_vm(self, out_wav: Path, timeout_ms: int) -> Path:
        # The VM is configured (in qemu-raspi.yaml) to write its speaker
        # to out_wav. We just snapshot it here.
        if self.out_wav.exists():
            shutil.copyfile(self.out_wav, out_wav)
        return out_wav


def from_yaml(audio_backend_section: dict) -> AudioBackend:
    kind = audio_backend_section.get("type", "portaudio-file")
    if kind == "pipewire":
        pw = audio_backend_section.get("pipewire", {})
        return PipeWireBackend(pw.get("null_sink_name", "blazen-vm-null"),
                               pw.get("capture_node", "blazen-vm-capture"))
    if kind == "portaudio-file":
        pf = audio_backend_section.get("portaudio_file", {})
        return PortAudioFileBackend(Path(pf.get("in_wav", "/tmp/blazen-vm-in.wav")),
                                    Path(pf.get("out_wav", "/tmp/blazen-vm-out.wav")))
    raise ValueError(f"unknown audio backend: {kind!r}")
