"""Tier 0 — the on-device LLM client (offline; no binding, no model load).

All tests inject a fake backend so they run with neither llama-cpp-python nor a
2 GB GGUF present — `make test-fast` stays fast and hermetic.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from blazend.assistant.localllm import (
    LlmError,
    LocalLlm,
    _LlamaCppBackend,
    resolve_model_path,
)
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


# ---------------------------------------------------------------------------
# _LlamaCppBackend — driven with a fake `llama_cpp.Llama` (no real model).
# ---------------------------------------------------------------------------


class _FakeLlama:
    """Stand-in for llama_cpp.Llama: canned chat completions, sync + stream."""

    last_kwargs: dict = {}

    def __init__(self, **kwargs) -> None:
        _FakeLlama.last_kwargs = kwargs

    def create_chat_completion(self, *, stream: bool = False, **_kw):
        if not stream:
            return {"choices": [{"message": {"content": "  Cześć!  "}}]}
        chunks = [
            {"choices": [{"delta": {"content": "Cześć"}}]},
            {"choices": [{"delta": {"content": ". "}}]},
            {"choices": [{"delta": {}}]},  # no content → skipped
            {"choices": [{"delta": {"content": "Jak?"}}]},
        ]
        return iter(chunks)


@pytest.fixture
def _fake_llama_cpp(monkeypatch):
    """Inject a fake `llama_cpp` module so the lazy import resolves to it."""
    import importlib.machinery

    mod = types.ModuleType("llama_cpp")
    mod.Llama = _FakeLlama
    mod.__spec__ = importlib.machinery.ModuleSpec("llama_cpp", loader=None)
    monkeypatch.setitem(sys.modules, "llama_cpp", mod)
    return mod


def _backend(tmp_path) -> _LlamaCppBackend:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"\x00")
    return _LlamaCppBackend(
        model,
        n_ctx=2048,
        n_threads=4,
        n_batch=128,
        use_mmap=True,
        use_mlock=False,
        temperature=0.4,
        top_p=0.9,
        top_k=40,
        repeat_penalty=1.1,
        seed=0,
        max_tokens=64,
    )


def test_llamacpp_backend_generate(_fake_llama_cpp, tmp_path):
    backend = _backend(tmp_path)
    assert backend.generate(system="S", user="hej") == "Cześć!"  # stripped
    assert _FakeLlama.last_kwargs["n_ctx"] == 2048
    assert _FakeLlama.last_kwargs["n_gpu_layers"] == 0


def test_llamacpp_backend_generate_stream(_fake_llama_cpp, tmp_path):
    backend = _backend(tmp_path)
    chunks = list(backend.generate_stream(system="S", user="hej"))
    assert chunks == ["Cześć", ". ", "Jak?"]  # empty delta dropped


def test_local_llm_uses_llamacpp_backend_when_model_present(_fake_llama_cpp, tmp_path, monkeypatch):
    """End-to-end through LocalLlm: real `_ensure_backend` builds the llama backend."""
    monkeypatch.setenv("BLAZEN_MODELS_DIR", str(tmp_path))
    cfg = load("llm")
    active = cfg.get("active_model")
    file = cfg.get("models", {})[active]["cpu"]["file"]
    model = tmp_path / "llm" / active / file
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_bytes(b"\x00")

    llm = LocalLlm()
    assert llm.available is True
    assert llm.chat("hej") == "Cześć!"
    assert list(llm.chat_stream("hej")) == ["Cześć", ". ", "Jak?"]


def test_ensure_backend_raises_without_active_model(monkeypatch):
    """No active_model in config → LlmError, not a crash."""

    def fake_load(_name):
        from blazend.config import Config

        return Config(name="llm", data={}, sources=[])

    monkeypatch.setattr("blazend.assistant.localllm.load", fake_load)
    llm = LocalLlm()
    assert llm.available is False
    with pytest.raises(LlmError, match="no active_model"):
        llm.chat("hej")


def test_ensure_backend_raises_when_model_file_missing(monkeypatch, tmp_path):
    """active_model set but the GGUF is absent → LlmError."""
    monkeypatch.setenv("BLAZEN_MODELS_DIR", str(tmp_path))  # empty → file missing
    llm = LocalLlm()
    with pytest.raises(LlmError, match="not found"):
        llm.chat("hej")
