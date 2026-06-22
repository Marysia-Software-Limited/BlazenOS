"""Tier 0 — the on-device LLM client (offline; no binding, no model load).

All tests inject a fake backend so they run with neither llama-cpp-python nor a
2 GB GGUF present — `make test-fast` stays fast and hermetic.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from blazend.assistant.localllm import LocalLlm, resolve_model_path
from blazend.config import load

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _config_root(monkeypatch):
    monkeypatch.setenv("BLAZEN_CONFIG_ROOT", str(REPO / "configs"))


class FakeLlm:
    """Implements the LlmBackend Protocol (generate only — no streaming)."""

    def __init__(self, reply: str = "ok") -> None:
        self.reply = reply
        self.calls: list[tuple[str, str]] = []

    def generate(self, *, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self.reply


class FakeStreamLlm(FakeLlm):
    """Also implements the optional generate_stream seam."""

    def __init__(self, chunks: list[str]) -> None:
        super().__init__("".join(chunks))
        self.chunks = chunks

    def generate_stream(self, *, system: str, user: str):
        self.calls.append((system, user))
        yield from self.chunks


def test_available_with_injected_backend():
    llm = LocalLlm(backend=FakeLlm())
    assert llm.available
    assert llm.chat("cześć", system="S") == "ok"


def test_system_prompt_read_from_config():
    llm = LocalLlm(backend=FakeLlm())
    assert "Jessica" in llm.system_prompt


def test_chat_uses_default_system_prompt_when_none():
    fake = FakeLlm("hej")
    llm = LocalLlm(backend=fake)
    llm.chat("co słychać?")
    system, user = fake.calls[0]
    assert "Jessica" in system and user == "co słychać?"


def test_resolve_model_path_matches_install_layout(monkeypatch, tmp_path):
    monkeypatch.delenv("BLAZEN_LLM_MODEL", raising=False)
    monkeypatch.setenv("BLAZEN_MODELS_DIR", str(tmp_path))
    cfg = load("llm")
    active = cfg.get("active_model")
    file = cfg.get("models", {})[active]["cpu"]["file"]
    assert resolve_model_path(cfg) == tmp_path / "llm" / active / file


def test_unavailable_when_no_model_and_no_injected_backend(monkeypatch, tmp_path):
    # Point the model root at an empty dir → the GGUF is absent → unavailable,
    # regardless of whether the binding is importable.
    monkeypatch.setenv("BLAZEN_MODELS_DIR", str(tmp_path))
    assert LocalLlm().available is False


def test_chat_stream_yields_backend_chunks():
    fake = FakeStreamLlm(["Cześć", ". ", "Jak ", "się ", "masz?"])
    llm = LocalLlm(backend=fake)
    chunks = list(llm.chat_stream("hej", system="S"))
    assert chunks == ["Cześć", ". ", "Jak ", "się ", "masz?"]
    assert "".join(chunks) == "Cześć. Jak się masz?"


def test_chat_stream_falls_back_to_single_chunk_without_streaming():
    # A backend with only generate() → chat_stream yields the whole reply once.
    fake = FakeLlm("Jedno zdanie.")
    llm = LocalLlm(backend=fake)
    assert list(llm.chat_stream("hej")) == ["Jedno zdanie."]
