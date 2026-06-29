"""Unit tests for the Phase 4 thin LLM inference server.

The server does no routing/memory — it runs the model for a `brain.request`
and answers `brain.reply`, honouring the mind's `backend` hint and escalating
local → OpenAI → Gemini exactly like the old engine.
"""

from __future__ import annotations

from blazend.domains.ai_orchestrator.adapters.rpi5.inference import InferenceServer
from blazend.events import TOPIC_BRAIN_REPLY, TOPIC_BRAIN_REQUEST, Envelope


class _Llm:
    """Minimal stand-in for an LlmPort / cloud client."""

    def __init__(self, *, available: bool, answer: str) -> None:
        self._available = available
        self._answer = answer
        self.calls: list[tuple[str, str | None]] = []

    @property
    def available(self) -> bool:
        return self._available

    def chat(self, user: str, *, system: str | None = None) -> str:
        self.calls.append((user, system))
        return self._answer


def _server(primary: object, openai: object, gemini: object) -> InferenceServer:
    return InferenceServer(primary=primary, openai=openai, gemini=gemini)  # type: ignore[arg-type]


def test_on_device_first_when_available() -> None:
    local = _Llm(available=True, answer="Bielik mówi cześć")
    oai = _Llm(available=True, answer="openai")
    srv = _server(local, oai, _Llm(available=False, answer=""))
    out = srv.infer(prompt="cześć", system="persona", backend="local", lang="pl")
    assert out == "Bielik mówi cześć"
    assert local.calls == [("cześć", "persona")]
    assert oai.calls == []  # not reached


def test_escalates_when_primary_unavailable() -> None:
    local = _Llm(available=False, answer="")
    oai = _Llm(available=True, answer="openai answer")
    srv = _server(local, oai, _Llm(available=False, answer=""))
    out = srv.infer(prompt="hi", system=None, backend="local", lang="en")
    assert out == "openai answer"


def test_backend_hint_reorders_candidates() -> None:
    local = _Llm(available=True, answer="local")
    gem = _Llm(available=True, answer="gemini answer")
    srv = _server(local, _Llm(available=False, answer=""), gem)
    # Hinting gemini tries it first even though local is available.
    out = srv.infer(prompt="q", system=None, backend="gemini", lang="en")
    assert out == "gemini answer"
    assert local.calls == []


def test_canned_reply_when_nothing_available() -> None:
    none = _Llm(available=False, answer="")
    srv = _server(none, _Llm(available=False, answer=""), _Llm(available=False, answer=""))
    pl = srv.infer(prompt="x", system=None, backend=None, lang="pl")
    en = srv.infer(prompt="x", system=None, backend=None, lang="en")
    assert "model" in pl.lower()
    assert "model" in en.lower()


def test_reply_for_builds_brain_reply_with_request_id() -> None:
    local = _Llm(available=True, answer="odpowiedź")
    srv = _server(local, _Llm(available=False, answer=""), _Llm(available=False, answer=""))
    req = Envelope(
        topic=TOPIC_BRAIN_REQUEST,
        source="blazend-mind",
        data={"request_id": "mind-7", "language": "pl", "prompt": "cześć", "system": "p", "backend": "local"},
    )
    reply = srv.reply_for(req)
    assert reply.topic == TOPIC_BRAIN_REPLY
    assert reply.data["request_id"] == "mind-7"
    assert reply.data["text"] == "odpowiedź"
    assert reply.data["final_"] is True
