"""Unit tests for the Phase 4d tool services + tool server.

Behaviour is ported from the old engine tool methods; these lock the spoken
phrasing and the tool.response shape with injected fakes (no network).
"""

from __future__ import annotations

from typing import Any

from blazend.domains.ai_orchestrator.adapters.rpi5.tool_server import ToolService
from blazend.domains.ai_orchestrator.adapters.rpi5.tools import Tools
from blazend.events import TOPIC_TOOL_REQUEST, TOPIC_TOOL_RESPONSE, Envelope


class _Note:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeMemory:
    def __init__(self, notes: list[str]) -> None:
        self._notes = [_Note(t) for t in notes]

    def notes(self) -> list[_Note]:
        return self._notes

    def pending(self) -> list[Any]:
        return []


class _Station:
    def __init__(self, sid: str, name: str, url: str) -> None:
        self.id, self.name, self.url = sid, name, url


class _FakeRadio:
    available = True

    def __init__(self, station: _Station | None) -> None:
        self._station = station
        self._all = [_Station("trojka", "Trójka", "http://x"), _Station("rmf", "RMF", "http://y")]

    def resolve(self, text: str) -> _Station | None:
        return self._station

    def offer(self, limit: int = 4) -> list[_Station]:
        return self._all


def _tools(**kw: Any) -> Tools:
    return Tools(**kw)  # type: ignore[arg-type]


def test_recall_notes_lists_recent() -> None:
    t = _tools(memory=_FakeMemory(["kup mleko", "oddać książkę"]))
    res = t.recall_notes("pl")
    assert res.ok
    assert "Pamiętam: kup mleko; oddać książkę." == res.text


def test_recall_notes_empty() -> None:
    t = _tools(memory=_FakeMemory([]))
    assert t.recall_notes("en").text == "I haven't noted anything yet."


def test_radio_play_named_station() -> None:
    t = _tools(radio=_FakeRadio(_Station("trojka", "Trójka", "http://x")))
    res = t.radio_play("trójka", "pl")
    assert res.action == "radio_play"
    assert res.text == "Włączam stację Trójka."
    assert res.payload["url"] == "http://x"


def test_radio_play_offers_when_unresolved() -> None:
    t = _tools(radio=_FakeRadio(None))
    res = t.radio_play("coś", "pl")
    assert res.action == "radio_offer"
    assert "Trójka" in res.text and "RMF" in res.text


def test_radio_stop() -> None:
    t = _tools(radio=_FakeRadio(None))
    assert t.radio_stop("en").text == "Turning off the radio."


def test_run_unknown_tool() -> None:
    t = _tools(memory=_FakeMemory([]))
    res = t.run("does.not.exist", {}, "en")
    assert not res.ok and res.action == "error"


def test_tool_service_builds_response_envelope() -> None:
    svc = ToolService(tools=_tools(radio=_FakeRadio(_Station("rmf", "RMF", "http://y"))))
    req = Envelope(
        topic=TOPIC_TOOL_REQUEST,
        source="blazend-mind",
        data={"request_id": "t-9", "tool": "radio.play", "language": "pl", "args": {"query": "rmf"}},
    )
    resp = svc.response_for(req)
    assert resp.topic == TOPIC_TOOL_RESPONSE
    assert resp.data["request_id"] == "t-9"
    assert resp.data["ok"] is True
    assert resp.data["action"] == "radio_play"
    assert resp.data["payload"]["name"] == "RMF"
