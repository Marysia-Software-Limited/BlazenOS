"""TTS backends: Apple `say` (on-device, default) and Azure Neural (premium opt-in).

Apple is the default renderer — fully on-device on Apple Silicon, free, no
secrets. Azure Neural (``pl-PL-MarekNeural``) is a per-book opt-in for a premium
shelf; its key comes from the gitignored ``macos/.secrets.env`` and is never
logged. Both satisfy the ``TtsBackend`` protocol so ``ingest`` is backend-blind.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Protocol


class TtsBackend(Protocol):
    def render_chapter(self, text: str, out_mp3: Path) -> None: ...


def apple_commands(text_path: Path, aiff_path: Path, mp3_path: Path,
                   voice: str) -> list[list[str]]:
    """Pure: the two argv lists that render ``text_path`` → mp3 via `say` then ffmpeg."""
    say = ["say", "-v", voice, "-f", str(text_path), "-o", str(aiff_path)]
    conv = ["ffmpeg", "-y", "-i", str(aiff_path), "-codec:a", "libmp3lame",
            "-qscale:a", "4", str(mp3_path)]
    return [say, conv]


def installed_polish_voice_is_compact(voice: str = "Zosia") -> bool:
    """True when only the compact ``voice`` is present (no Premium/Enhanced variant).

    Used to warn that audiobook quality benefits from downloading the enhanced
    Polish system voice. Returns False (no warning) if `say` can't be queried.
    """
    try:
        out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True,
                             check=False).stdout
    except (OSError, FileNotFoundError):
        return False
    lines = [ln for ln in out.splitlines() if voice.lower() in ln.lower()]
    if not lines:
        return False
    return not any(("premium" in ln.lower() or "enhanced" in ln.lower()) for ln in lines)


class AppleTTS:
    """On-device macOS speech synthesis via the `say` command → MP3 via ffmpeg."""

    def __init__(self, voice: str = "Zosia") -> None:
        self.voice = voice

    def render_chapter(self, text: str, out_mp3: Path) -> None:
        out_mp3.parent.mkdir(parents=True, exist_ok=True)
        txt = out_mp3.with_suffix(".txt")
        aiff = out_mp3.with_suffix(".aiff")
        txt.write_text(text, encoding="utf-8")
        try:
            say, conv = apple_commands(txt, aiff, out_mp3, self.voice)
            subprocess.run(say, check=True)
            subprocess.run(conv, check=True, capture_output=True)
        finally:
            txt.unlink(missing_ok=True)
            aiff.unlink(missing_ok=True)


class XttsTTS:
    """High-quality Polish via a remote GPU XTTS-v2 server (paul's RTX 3090).

    POSTs chapter text to ``scripts/xtts_server.py`` (default
    ``http://192.168.50.102:8091/synthesize``) and converts the returned WAV to
    MP3. Free (your GPU), far more natural than Apple's on-device voices, and
    stays in-constellation. ``speaker`` picks a built-in XTTS speaker; voice
    cloning is configured server-side via ``BLAZEN_XTTS_SPEAKER_WAV``.
    """

    def __init__(self, url: str, *, language: str = "pl", speaker: str | None = None,
                 timeout: int = 600) -> None:
        self.url, self.language, self.speaker, self.timeout = url, language, speaker, timeout

    def render_chapter(self, text: str, out_mp3: Path) -> None:
        import json  # noqa: PLC0415
        import urllib.request  # noqa: PLC0415

        out_mp3.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, str] = {"text": text, "language": self.language}
        if self.speaker:
            payload["speaker"] = self.speaker
        req = urllib.request.Request(  # noqa: S310 (fixed LAN host, our own service)
            self.url, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:  # noqa: S310
            wav = r.read()
        wav_path = out_mp3.with_suffix(".wav")
        wav_path.write_bytes(wav)
        try:
            subprocess.run(["ffmpeg", "-y", "-i", str(wav_path), "-codec:a", "libmp3lame",
                            "-qscale:a", "4", str(out_mp3)], check=True, capture_output=True)
        finally:
            wav_path.unlink(missing_ok=True)


class AzureTTS:
    """Azure Neural pl-PL rendering — a premium, opt-in cloud renderer."""

    def __init__(self, *, key: str, region: str = "westeurope",
                 voice: str = "pl-PL-MarekNeural") -> None:
        self.key, self.region, self.voice = key, region, voice

    def render_chapter(self, text: str, out_mp3: Path) -> None:
        import azure.cognitiveservices.speech as sdk  # noqa: PLC0415

        out_mp3.parent.mkdir(parents=True, exist_ok=True)
        cfg = sdk.SpeechConfig(subscription=self.key, region=self.region)
        cfg.speech_synthesis_voice_name = self.voice
        cfg.set_speech_synthesis_output_format(
            sdk.SpeechSynthesisOutputFormat.Audio24Khz96KBitRateMonoMp3)
        out = sdk.audio.AudioOutputConfig(filename=str(out_mp3))
        sdk.SpeechSynthesizer(speech_config=cfg, audio_config=out).speak_text_async(text).get()


__all__ = ["AppleTTS", "AzureTTS", "TtsBackend", "XttsTTS", "apple_commands",
           "installed_polish_voice_is_compact"]
