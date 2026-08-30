"""Tier 0 — multi-turn ``history`` threading across the LLM clients (offline).

Conversation mode (2026-08-30): every client's ``chat``/``chat_stream`` accepts
``history: Sequence[tuple[str, str]] | None`` — (user, assistant) turns, oldest
first — spliced between the system message and the current user message. The
safety invariant under test: **falsy history builds exactly the same messages
as before**, and :class:`LocalLlm` / :class:`_LocalSlot` forward the kwarg to
their backend only when it is truthy, so pre-history fakes keep working.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.gemini import GeminiClient
from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.mesh_openai import MeshOpenAiLlm
from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.ollama import OllamaLlm
from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.openai import OpenAiClient
from blazend.domains.ai_orchestrator.core.model_router import _LocalSlot
from blazend.domains.local_ai.adapters.rpi5.localllm import LocalLlm

REPO = Path(__file__).resolve().parents[3]

HISTORY = [("pierwsze pytanie", "pierwsza odpowiedź"), ("drugie pytanie", "druga odpowiedź")]


@pytest.fixture(autouse=True)
def _config_root(monkeypatch):
    monkeypatch.setenv("BLAZEN_CONFIG_ROOT", str(REPO / "configs"))


def _assert_spliced(msgs, *, assistant_role: str = "assistant") -> None:
    """Exact role ordering: system?, u1, a1, u2, a2, current-user."""
    if msgs[0]["role"] == "system":
        msgs = msgs[1:]
    assert [m["role"] for m in msgs] == ["user", assistant_role, "user", assistant_role, "user"]
    assert [m["content"] for m in msgs] == [
        "pierwsze pytanie",
        "pierwsza odpowiedź",
        "drugie pytanie",
        "druga odpowiedź",
        "teraz",
    ]


# --- OpenAI-message-shaped clients (ollama / mesh_openai / openai) ----------
@pytest.mark.parametrize(
    "client",
    [
        OllamaLlm(url="http://gpu.lan:11434"),
        MeshOpenAiLlm("http://peer.lan:11436"),
        OpenAiClient(api_key="test-key"),
    ],
    ids=["ollama", "mesh_openai", "openai"],
)
def test_messages_without_history_unchanged(client):
    # The pre-history shape, byte for byte: system? + current user.
    assert client._messages("teraz", "S") == [
        {"role": "system", "content": "S"},
        {"role": "user", "content": "teraz"},
    ]
    assert client._messages("teraz", None) == [{"role": "user", "content": "teraz"}]
    # Empty history is falsy → identical to no history.
    assert client._messages("teraz", "S", []) == client._messages("teraz", "S")


@pytest.mark.parametrize(
    "client",
    [
        OllamaLlm(url="http://gpu.lan:11434"),
        MeshOpenAiLlm("http://peer.lan:11436"),
        OpenAiClient(api_key="test-key"),
    ],
    ids=["ollama", "mesh_openai", "openai"],
)
def test_messages_splice_history_between_system_and_user(client):
    msgs = client._messages("teraz", "S", HISTORY)
    assert msgs[0] == {"role": "system", "content": "S"}
    _assert_spliced(msgs)
    # Without a system prompt the turns simply lead.
    _assert_spliced(client._messages("teraz", None, HISTORY))


def test_openai_chat_sends_spliced_history():
    sent: list[dict] = []

    def transport(_url, _headers, body):
        sent.append(body)
        return {"choices": [{"message": {"content": "ok"}}]}

    client = OpenAiClient(api_key="test-key", transport=transport)
    assert client.chat("teraz", system="S", history=HISTORY) == "ok"
    _assert_spliced(sent[0]["messages"])
    # chat_stream threads the same history through chat().
    assert list(client.chat_stream("teraz", system="S", history=HISTORY)) == ["ok"]
    _assert_spliced(sent[1]["messages"])


def test_openai_chat_without_history_body_unchanged():
    sent: list[dict] = []

    def transport(_url, _headers, body):
        sent.append(body)
        return {"choices": [{"message": {"content": "ok"}}]}

    client = OpenAiClient(api_key="test-key", transport=transport)
    client.chat("teraz", system="S")
    assert sent[0]["messages"] == [
        {"role": "system", "content": "S"},
        {"role": "user", "content": "teraz"},
    ]


# --- Gemini (role "model", parts-shaped contents) ---------------------------
def test_gemini_chat_builds_alternating_contents():
    sent: list[dict] = []

    def transport(_url, body):
        sent.append(body)
        return {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}

    client = GeminiClient(api_key="test-key", transport=transport)
    assert client.chat("teraz", system="S", history=HISTORY) == "ok"
    contents = sent[0]["contents"]
    assert [c["role"] for c in contents] == ["user", "model", "user", "model", "user"]
    assert [c["parts"][0]["text"] for c in contents] == [
        "pierwsze pytanie",
        "pierwsza odpowiedź",
        "drugie pytanie",
        "druga odpowiedź",
        "teraz",
    ]
    assert sent[0]["systemInstruction"] == {"parts": [{"text": "S"}]}


def test_gemini_chat_without_history_unchanged():
    sent: list[dict] = []

    def transport(_url, body):
        sent.append(body)
        return {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}

    client = GeminiClient(api_key="test-key", transport=transport)
    client.chat("teraz")
    assert sent[0]["contents"] == [{"role": "user", "parts": [{"text": "teraz"}]}]


# --- LocalLlm: conditional forwarding to the backend seam -------------------
class _LegacyBackend:
    """Pre-history fake: generate(*, system, user) only — must keep working."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def generate(self, *, system: str, user: str) -> str:
        self.calls.append((system, user))
        return "ok"


class _HistoryBackend:
    """History-aware fake: records what the kwarg carried."""

    def __init__(self) -> None:
        self.histories: list[object] = []

    def generate(self, *, system: str, user: str, history=None) -> str:
        self.histories.append(history)
        return "ok"

    def generate_stream(self, *, system: str, user: str, history=None):
        self.histories.append(history)
        yield "ok"


def test_localllm_no_history_skips_kwarg_for_legacy_backend():
    fake = _LegacyBackend()
    llm = LocalLlm(backend=fake)
    assert llm.chat("teraz", system="S") == "ok"
    assert llm.chat("teraz", system="S", history=[]) == "ok"  # empty is falsy too
    assert list(llm.chat_stream("teraz", system="S")) == ["ok"]
    assert fake.calls == [("S", "teraz")] * 3


def test_localllm_forwards_truthy_history():
    fake = _HistoryBackend()
    llm = LocalLlm(backend=fake)
    assert llm.chat("teraz", system="S", history=HISTORY) == "ok"
    assert list(llm.chat_stream("teraz", system="S", history=HISTORY)) == ["ok"]
    assert llm.chat("teraz", system="S") == "ok"  # no history → kwarg omitted → default None
    assert fake.histories == [HISTORY, HISTORY, None]


def test_localllm_backend_messages_splice():
    # The real llama.cpp backend's message builder follows the same convention.
    from blazend.domains.local_ai.adapters.rpi5.localllm import _LlamaCppBackend

    msgs = _LlamaCppBackend._messages(object(), "S", "teraz", HISTORY)
    assert msgs[0] == {"role": "system", "content": "S"}
    _assert_spliced(msgs)
    assert _LlamaCppBackend._messages(object(), "S", "teraz") == [
        {"role": "system", "content": "S"},
        {"role": "user", "content": "teraz"},
    ]


# --- _LocalSlot: router forwarders stay backward-compatible -----------------
class _LegacyLlm:
    """Pre-history Llm fake (chat/chat_stream without the kwarg)."""

    available = True

    def __init__(self) -> None:
        self.calls: list[str] = []

    def chat(self, user: str, *, system: str | None = None) -> str:
        self.calls.append(user)
        return "ok"

    def chat_stream(self, user: str, *, system: str | None = None):
        yield self.chat(user, system=system)


class _HistoryLlm(_LegacyLlm):
    """History-aware Llm fake."""

    def __init__(self) -> None:
        super().__init__()
        self.histories: list[object] = []

    def chat(self, user: str, *, system: str | None = None, history=None) -> str:
        self.histories.append(history)
        return super().chat(user, system=system)

    def chat_stream(self, user: str, *, system: str | None = None, history=None):
        yield self.chat(user, system=system, history=history)


class _StubRouter:
    """Just the eviction hook _LocalSlot touches."""

    def _activate_local(self, name: str) -> None:  # noqa: ARG002
        pass


def test_local_slot_no_history_works_with_legacy_llm():
    slot = _LocalSlot("bielik-1.5b", _LegacyLlm(), _StubRouter())
    assert slot.chat("teraz", system="S") == "ok"
    assert list(slot.chat_stream("teraz", system="S", history=[])) == ["ok"]


def test_local_slot_forwards_truthy_history():
    fake = _HistoryLlm()
    slot = _LocalSlot("bielik-1.5b", fake, _StubRouter())
    assert slot.chat("teraz", system="S", history=HISTORY) == "ok"
    assert list(slot.chat_stream("teraz", system="S", history=HISTORY)) == ["ok"]
    assert fake.histories == [HISTORY, HISTORY]
