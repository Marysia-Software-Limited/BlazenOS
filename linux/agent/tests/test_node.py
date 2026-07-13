"""Unit tests for the Linux node's mesh-wired agent build (no network)."""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from blazend.domains.ai_orchestrator.core.model_router import Task
from mesh_registry import Mesh

from jessica_linux.node import _NoLocalModel, build_router

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _config_root(monkeypatch):
    # build_router() constructs a ModelRouter, which loads llm.yaml routing.
    monkeypatch.setenv("BLAZEN_CONFIG_ROOT", str(REPO / "configs"))


def _mesh_with_ollama(url: str = "http://192.168.50.102:11434") -> Mesh:
    data = {
        "nodes": {
            "paul": {
                "host": "192.168.50.102",
                "resources": {"llm": {"ollama-11b": {"kind": "openai", "url": url}}},
            }
        }
    }
    return Mesh(data, self_node="paul")


class _FakeLlm:
    """An always-available backend that echoes, standing in for Ollama/OpenAI."""

    def __init__(self, tag: str, *, available: bool = True) -> None:
        self.tag = tag
        self._available = available

    @property
    def available(self) -> bool:
        return self._available

    def chat(self, user: str, *, system: str | None = None) -> str:
        return f"{self.tag}:{user}"

    def chat_stream(self, user: str, *, system: str | None = None) -> Iterator[str]:
        yield self.chat(user, system=system)

    def close(self) -> None:
        pass


def test_router_resolves_ollama_from_mesh_and_routes_to_it():
    ollama = _FakeLlm("ollama")
    router = build_router(_mesh_with_ollama(), ollama=ollama, openai=_FakeLlm("gpt", available=False))
    # Node-local policy: every task runs on THIS node's GPU Ollama (the shared
    # llm.yaml routes the Pi to its on-device Bielik instead); it's up here.
    name, backend = router.first(Task.COMMAND)
    assert name == "ollama-11b"
    assert backend.chat("hej") == "ollama:hej"
    assert router.first(Task.RECOMMEND)[0] == "ollama-11b"


def test_local_tier_is_unavailable_on_a_linux_node():
    # With no injected ollama and no reachable endpoint, the local tiers must be
    # the _NoLocalModel stand-in (never available), so the node never tries to
    # load a llama.cpp Bielik it doesn't have.
    stub = _NoLocalModel("bielik-1.5b")
    assert stub.available is False
    with pytest.raises(RuntimeError):
        stub.chat("hej")
