"""Tier 0 — the Telegram bridge (offline; injected transport)."""
from __future__ import annotations

import json

from blazend.domains.systems.adapters.rpi5.telegram import (
    Bridge,
    TelegramApi,
    parse_update,
)


class _FakeHttp:
    """Scripted Bot-API endpoint: records calls, serves canned results."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.file_bytes = b"OGGDATA"

    def __call__(self, url: str, data: bytes | None, timeout: float) -> bytes:
        method = url.rsplit("/", 1)[-1]
        if "/file/" in url:
            return self.file_bytes
        params = json.loads(data.decode()) if data else {}
        self.calls.append((method, params))
        if method == "getFile":
            return json.dumps({"ok": True, "result": {"file_path": "voice/x.oga"}}).encode()
        return json.dumps({"ok": True, "result": {}}).encode()


def _update(chat=111, **msg):
    return {"update_id": 7,
            "message": {"chat": {"id": chat}, "from": {"first_name": "Beret"}, **msg}}


def test_parse_update_normalises_text_and_voice():
    t = parse_update(_update(text="cześć"))
    assert t and (t.kind, t.text, t.sender) == ("text", "cześć", "Beret")
    v = parse_update(_update(voice={"file_id": "F1", "duration": 9}))
    assert v and (v.kind, v.file_id, v.duration_s) == ("voice", "F1", 9)
    assert parse_update({"edited_message": {}}) is None


def test_allowlisted_text_is_spoken_and_confirmed():
    http = _FakeHttp()
    spoken: list[tuple[str, str]] = []
    bridge = Bridge(TelegramApi("tok", http), [111],
                    on_text=lambda s, t: spoken.append((s, t)),
                    on_voice=lambda s, b: None)
    assert bridge.handle(_update(text="obiad gotowy")) == "spoken"
    assert spoken == [("Beret", "obiad gotowy")]
    assert http.calls[-1][0] == "sendMessage"
    assert "✓" in http.calls[-1][1]["text"]


def test_stranger_is_ignored_without_any_reply():
    http = _FakeHttp()
    bridge = Bridge(TelegramApi("tok", http), [111],
                    on_text=lambda s, t: (_ for _ in ()).throw(AssertionError),
                    on_voice=lambda s, b: None)
    assert bridge.handle(_update(chat=999, text="kto tu mieszka?")) == "rejected"
    assert http.calls == []  # no sendMessage — the bot stays silent


def test_voice_note_is_downloaded_and_played():
    http = _FakeHttp()
    played: list[tuple[str, bytes]] = []
    bridge = Bridge(TelegramApi("tok", http), [111],
                    on_text=lambda s, t: None,
                    on_voice=lambda s, b: played.append((s, b)))
    verdict = bridge.handle(_update(voice={"file_id": "F1", "duration": 8}))
    assert verdict == "played"
    assert played == [("Beret", b"OGGDATA")]


def test_overlong_voice_note_is_refused():
    http = _FakeHttp()
    bridge = Bridge(TelegramApi("tok", http), [111],
                    on_text=lambda s, t: None,
                    on_voice=lambda s, b: (_ for _ in ()).throw(AssertionError))
    verdict = bridge.handle(_update(voice={"file_id": "F1", "duration": 600}))
    assert verdict == "too_long"
    assert "✗" in http.calls[-1][1]["text"]
