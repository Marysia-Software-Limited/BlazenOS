"""Telegram bridge — first external comms transport (2026-09-23).

User request 2026-09-22: after node↔node calls, Jessica should be reachable
from the outside world — "telegram bądź dowolny inny". This bridge is the
Bot-API incarnation: a per-node daemon long-polls ``getUpdates`` (works
behind GSM/CGNAT — no webhook, no inbound port) and delivers messages from
an **allowlisted** set of chats to the room:

- a text message is SPOKEN through the announce spool
  ("Wiadomość z Telegrama od …: …");
- a voice message (OGG/Opus) is DECODED (``opusdec``, fallback ``ffmpeg``)
  and PLAYED through the speaker after a spoken intro — the sender's real
  voice in the room, not a transcript.

The sender gets a ✓ confirmation; a chat NOT on the allowlist is ignored
(logged with its id so the operator can extend ``telegram.yaml`` — never
answered, so the bot does not advertise its owner). The bot token lives in
``secrets.env`` (``BLAZEN_TELEGRAM_TOKEN``), never in git.

Real Telegram *calls* are MTProto territory (a bot cannot make them) — this
bridge deliberately starts with messages; ``intercom.yaml``'s peer seam is
where a future call-capable transport plugs in.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

_API = "https://api.telegram.org"
# Spoken-text cap for an incoming text message (announce channel speaks it).
_TEXT_MAX = 500
# Voice notes longer than this are refused (the room is not a podcast venue).
_VOICE_MAX_S = 120


class TelegramError(RuntimeError):
    """Bot API failure (transport or ok=false)."""


# http(url, data, timeout) -> raw response bytes; injectable for tests.
Http = Callable[[str, bytes | None, float], bytes]


def _urllib_http(url: str, data: bytes | None, timeout: float) -> bytes:
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return bytes(resp.read())
    except urllib.error.URLError as e:  # pragma: no cover - network path
        raise TelegramError(f"telegram unreachable: {e}") from e


class TelegramApi:
    """Thin Bot-API client (long-poll getUpdates, sendMessage, file download)."""

    def __init__(self, token: str, http: Http | None = None) -> None:
        if not token:
            raise TelegramError("no bot token (BLAZEN_TELEGRAM_TOKEN)")
        self._token = token
        self._http = http or _urllib_http

    def call(self, method: str, params: dict[str, Any] | None = None,
             *, timeout: float = 30.0) -> Any:
        raw = self._http(f"{_API}/bot{self._token}/{method}",
                         json.dumps(params or {}).encode(), timeout)
        try:
            body = json.loads(raw.decode("utf-8"))
        except ValueError as e:
            raise TelegramError(f"bad API response: {e}") from e
        if not body.get("ok"):
            raise TelegramError(f"{method}: {body.get('description', 'not ok')}")
        return body.get("result")

    def get_updates(self, offset: int, timeout_s: int) -> list[dict[str, Any]]:
        result = self.call("getUpdates",
                           {"offset": offset, "timeout": timeout_s,
                            "allowed_updates": ["message"]},
                           timeout=timeout_s + 15.0)
        return list(result or [])

    def send_message(self, chat_id: int, text: str) -> None:
        self.call("sendMessage", {"chat_id": chat_id, "text": text})

    def download(self, file_id: str) -> bytes:
        info = self.call("getFile", {"file_id": file_id})
        path = str((info or {}).get("file_path", ""))
        if not path:
            raise TelegramError("getFile returned no file_path")
        return self._http(f"{_API}/file/bot{self._token}/{path}", None, 120.0)


@dataclass
class Incoming:
    """One normalised inbound message."""

    chat_id: int
    sender: str
    kind: str            # "text" | "voice" | "other"
    text: str = ""
    file_id: str = ""
    duration_s: int = 0


def parse_update(update: dict[str, Any]) -> Incoming | None:
    """Normalise a Bot-API update; None for anything that is not a message."""
    m = update.get("message")
    if not isinstance(m, dict):
        return None
    chat = m.get("chat") or {}
    sender = str((m.get("from") or {}).get("first_name", "") or "ktoś")
    chat_id = int(chat.get("id", 0))
    if m.get("voice"):
        voice = m["voice"]
        return Incoming(chat_id, sender, "voice",
                        file_id=str(voice.get("file_id", "")),
                        duration_s=int(voice.get("duration", 0)))
    if m.get("text"):
        return Incoming(chat_id, sender, "text", text=str(m["text"]))
    return Incoming(chat_id, sender, "other")


class Bridge:
    """Routes allowlisted messages to the room; confirms back to the sender.

    Side effects are injected (``on_text`` speaks, ``on_voice`` plays), so
    the routing logic stays a pure-ish, unit-tested seam."""

    def __init__(self, api: TelegramApi, allowed_chats: list[int],
                 on_text: Callable[[str, str], None],
                 on_voice: Callable[[str, bytes], None]) -> None:
        self._api = api
        self._allowed = set(int(c) for c in allowed_chats)
        self._on_text = on_text
        self._on_voice = on_voice

    def handle(self, update: dict[str, Any]) -> str:
        msg = parse_update(update)
        if msg is None:
            return "skipped"
        if msg.chat_id not in self._allowed:
            # Never answer: an unlisted stranger must not learn whose bot
            # this is. The log line carries the id the operator needs to
            # extend telegram.yaml's allowed_chats.
            log.warning("telegram: ignoring chat %s (%s) — not allowlisted",
                        msg.chat_id, msg.sender)
            return "rejected"
        if msg.kind == "text":
            self._on_text(msg.sender, msg.text[:_TEXT_MAX])
            self._api.send_message(msg.chat_id, "✓ przeczytane na głos u Jessiki")
            return "spoken"
        if msg.kind == "voice":
            if msg.duration_s > _VOICE_MAX_S:
                self._api.send_message(
                    msg.chat_id,
                    f"✗ wiadomość dłuższa niż {_VOICE_MAX_S} s — nie odtworzę")
                return "too_long"
            self._on_voice(msg.sender, self._api.download(msg.file_id))
            self._api.send_message(msg.chat_id, "✓ odtworzone u Jessiki")
            return "played"
        self._api.send_message(
            msg.chat_id, "✗ obsługuję tylko tekst i wiadomości głosowe")
        return "unsupported"


__all__ = ["Bridge", "Incoming", "TelegramApi", "TelegramError", "parse_update"]
