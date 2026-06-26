"""Tier 0 — the ai-orchestrator backend registry (offline; no model load).

``select_chat_llm`` picks the primary chat backend the brain talks to. These
tests drive every branch (default local, reachable remote, unreachable remote,
build failure) with fakes so no GGUF loads and no network is touched.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from blazend.assistant.localllm import LocalLlm
from blazend.domains.ai_orchestrator.core import registry
from blazend.domains.ai_orchestrator.core.registry import select_chat_llm

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _config_root(monkeypatch):
    monkeypatch.setenv("BLAZEN_CONFIG_ROOT", str(REPO / "configs"))
    monkeypatch.delenv("BLAZEN_LLM_OLLAMA_URL", raising=False)


class _FakeOllama:
    """Stand-in for OllamaLlm with a togglable reachability."""

    url = "http://gpu.box:11434"
    model = "bielik-11b"

    def __init__(self, available: bool) -> None:
        self._available = available

    @property
    def available(self) -> bool:
        return self._available

    def chat(self, user: str, *, system: str | None = None) -> str:
        return ""

    def chat_stream(self, user: str, *, system: str | None = None) -> Iterator[str]:
        yield ""


def test_defaults_to_local_when_no_ollama_url():
    llm = select_chat_llm()
    assert isinstance(llm, LocalLlm)


def test_prefers_reachable_ollama(monkeypatch):
    monkeypatch.setenv("BLAZEN_LLM_OLLAMA_URL", "http://gpu.box:11434")
    monkeypatch.setattr(registry, "OllamaLlm", lambda _url: _FakeOllama(available=True))
    llm = select_chat_llm()
    assert getattr(llm, "url", None) == "http://gpu.box:11434"


def test_falls_back_to_local_when_ollama_unreachable(monkeypatch):
    monkeypatch.setenv("BLAZEN_LLM_OLLAMA_URL", "http://gpu.box:11434")
    monkeypatch.setattr(registry, "OllamaLlm", lambda _url: _FakeOllama(available=False))
    assert isinstance(select_chat_llm(), LocalLlm)


def test_returns_none_when_local_build_raises(monkeypatch):
    def boom() -> LocalLlm:
        raise RuntimeError("no binding")

    monkeypatch.setattr(registry, "LocalLlm", boom)
    assert select_chat_llm() is None
