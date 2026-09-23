"""blazend-telegram — the Telegram bridge daemon (see package docstring).

Wiring: incoming text → announce spool (the orchestrator speaks it through
the normal TTS path); incoming voice → spoken intro, then the OGG/Opus note
is decoded (``opusdec``, fallback ``ffmpeg``) and played through
``jabra_out`` under the ``speaking`` marker (wake/ASR must not hear the
sender's voice as a command — same rule as the intercom legs).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
import uuid

from blazend.config import load
from blazend.domains.systems.adapters.rpi5.telegram import (
    Bridge,
    TelegramApi,
    TelegramError,
)
from blazend.domains.voice_input.adapters.rpi5.intercom import _open_player
from blazend.ipc import runtime_dir

log = logging.getLogger("blazend.telegram")

_ANNOUNCE_GAP_S = 5.0  # let the spoken intro play before the voice note


def _announce(text: str) -> None:
    spool = runtime_dir() / "announce"
    spool.mkdir(exist_ok=True)
    name = f"tg-{int(time.time())}-{uuid.uuid4().hex[:6]}.json"
    tmp = spool / f"{name}.tmp"  # .tmp suffix: pathlib globs match dotfiles
    tmp.write_text(json.dumps({"text": text, "lang": "pl"}, ensure_ascii=False),
                   encoding="utf-8")
    tmp.rename(spool / name)


def _decode_ogg(ogg: bytes) -> bytes:
    """OGG/Opus → WAV via opusdec (tiny, in opus-tools), fallback ffmpeg."""
    if shutil.which("opusdec"):
        cmd = ["opusdec", "--quiet", "--rate", "48000", "-", "-"]
    elif shutil.which("ffmpeg"):
        cmd = ["ffmpeg", "-v", "quiet", "-i", "-", "-f", "wav", "-"]
    else:
        raise TelegramError("no opusdec/ffmpeg — install opus-tools")
    proc = subprocess.run(cmd, input=ogg, capture_output=True, timeout=60)
    if proc.returncode != 0 or not proc.stdout:
        raise TelegramError(f"voice decode failed: {proc.stderr[:120]!r}")
    return proc.stdout


def _play_voice(sender: str, ogg: bytes) -> None:
    wav = _decode_ogg(ogg)
    _announce(f"Wiadomość głosowa z Telegrama od: {sender}.")
    time.sleep(_ANNOUNCE_GAP_S)
    marker = runtime_dir() / "speaking"
    player = _open_player(["aplay", "-q", "-D", "jabra_out"])
    assert player.stdin is not None
    marker.touch()
    try:
        player.stdin.write(wav)
        player.stdin.close()
        player.wait(timeout=180)
    finally:
        marker.unlink(missing_ok=True)  # stale marker = deaf pipeline
        player.terminate()


def _speak_text(sender: str, text: str) -> None:
    _announce(f"Wiadomość z Telegrama od: {sender}. {text}")


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(name)s] %(message)s")
    try:
        cfg = load("telegram").data
    except Exception:  # noqa: BLE001 — no config → defaults (disabled)
        cfg = {}
    if not bool(cfg.get("enabled", False)):
        log.info("telegram bridge disabled (telegram.yaml enabled: false) — idle")
        while True:  # stay alive quietly: the unit is not an error state
            time.sleep(3600)
    token = os.environ.get("BLAZEN_TELEGRAM_TOKEN", "")
    allowed = [int(c) for c in (cfg.get("allowed_chats") or [])]
    poll_s = int(cfg.get("poll_timeout_s", 50))
    api = TelegramApi(token)
    bridge = Bridge(api, allowed, _speak_text, _play_voice)
    me = api.call("getMe")
    log.info("telegram bridge online as @%s (allowlist: %d chats)",
             (me or {}).get("username"), len(allowed))
    offset = 0
    while True:
        try:
            updates = api.get_updates(offset, poll_s)
        except TelegramError as exc:
            log.warning("poll failed (%s) — backing off", exc)
            time.sleep(10)
            continue
        for upd in updates:
            offset = max(offset, int(upd.get("update_id", 0)) + 1)
            try:
                verdict = bridge.handle(upd)
                log.info("update %s: %s", upd.get("update_id"), verdict)
            except TelegramError as exc:
                log.warning("update %s failed: %s", upd.get("update_id"), exc)


if __name__ == "__main__":
    raise SystemExit(main())
