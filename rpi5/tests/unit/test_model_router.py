"""Unit tests for the brain's task-based :class:`ModelRouter`."""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from blazend.domains.ai_orchestrator.core.model_router import ModelRouter, Task

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _config_root(monkeypatch):
    # Load the repo's real llm.yaml so routing tasks AND backend model names
    # (asserted by the eviction test) resolve — hermetic, not host /etc/blazen.
    monkeypatch.setenv("BLAZEN_CONFIG_ROOT", str(REPO / "configs"))


class _Fake:
    """A minimal ``Llm`` whose availability we control, tracking eviction."""

    def __init__(self, name: str, *, available: bool = True) -> None:
        self.name = name
        self._available = available
        self.closed = False

    @property
    def available(self) -> bool:
        return self._available

    def chat(self, user: str, *, system: str | None = None) -> str:
        return f"{self.name}:{user}"

    def chat_stream(self, user: str, *, system: str | None = None) -> Iterator[str]:
        yield self.chat(user, system=system)

    def close(self) -> None:
        self.closed = True


def _router(*, ollama_up: bool, key: bool):
    seen: dict[str, _Fake] = {}
    r = ModelRouter(
        ollama=_Fake("ollama", available=ollama_up),
        openai=_Fake("gpt", available=key),
        local_factory=lambda model: seen.setdefault(model, _Fake(model)),
    )
    return r, seen


def test_prefers_ollama_when_up():
    r, _ = _router(ollama_up=True, key=False)
    assert r.first(Task.COMMAND)[0] == "ollama-11b"
    assert r.first(Task.RECOMMEND)[0] == "ollama-11b"
    assert r.first(Task.OPEN_QA)[0] == "ollama-11b"  # no key → skip gpt-5.5


def test_local_tiers_when_ollama_down():
    r, _ = _router(ollama_up=False, key=False)
    assert r.first(Task.COMMAND)[0] == "bielik-1.5b"
    assert r.first(Task.RECOMMEND)[0] == "bielik-4.5b"
    assert r.first(Task.OPEN_QA)[0] == "bielik-4.5b"


def test_open_qa_prefers_gpt_when_key_present():
    r, _ = _router(ollama_up=False, key=True)
    assert r.first(Task.OPEN_QA)[0] == "gpt-5.5"
    # ...but a command never goes to the cloud.
    assert r.first(Task.COMMAND)[0] == "bielik-1.5b"


def test_single_local_eviction():
    r, seen = _router(ollama_up=False, key=False)
    _, cmd = r.first(Task.COMMAND)
    cmd.chat("hi")  # loads 1.5B
    _, rec = r.first(Task.RECOMMEND)
    rec.chat("polecaj")  # must evict 1.5B before 4.5B
    assert seen["bielik-1.5b-v3-instruct-q4_k_m"].closed
    assert not seen["bielik-4.5b-v3-instruct-q6_k"].closed


def test_route_yields_in_order_and_skips_unavailable():
    r, _ = _router(ollama_up=False, key=False)
    names = [n for n, _ in r.route(Task.OPEN_QA)]
    assert names == ["bielik-4.5b"]  # gpt-5.5 (no key) + ollama (down) skipped


def test_probe_cache_avoids_repeat_network_calls():
    calls = {"n": 0}

    class Probe(_Fake):
        @property
        def available(self) -> bool:  # counts probes
            calls["n"] += 1
            return True

    ticks = iter([0.0, 1.0, 2.0])
    r = ModelRouter(
        ollama=Probe("ollama"),
        openai=_Fake("gpt", available=False),
        local_factory=lambda m: _Fake(m),
        clock=lambda: next(ticks),
    )
    r.first(Task.COMMAND)
    r.first(Task.COMMAND)
    assert calls["n"] == 1  # second call within TTL used the cache


# -- P3: the mesh supplies the ollama-11b endpoint (the "where") ----------

def _mesh_with_ollama(url: str):
    from mesh_registry import Mesh
    return Mesh(
        {"nodes": {"paul": {"host": "192.168.50.102",
                            "resources": {"llm": {"ollama-11b": {"kind": "openai", "url": url}}}}}},
        self_node="paul",
    )


def test_ollama_url_resolved_from_the_mesh():
    r = ModelRouter(mesh=_mesh_with_ollama("http://192.168.50.102:11434"))  # no injected ollama
    backend = r._build("ollama-11b")
    assert backend is not None and backend.url == "http://192.168.50.102:11434"


def test_ollama_falls_back_to_env_when_absent_from_mesh(monkeypatch):
    from mesh_registry import Mesh
    monkeypatch.setenv("BLAZEN_LLM_OLLAMA_URL", "http://10.0.0.9:11434")
    r = ModelRouter(mesh=Mesh({"nodes": {"paul": {"host": "h", "resources": {}}}}, self_node="paul"))
    assert r._build("ollama-11b").url == "http://10.0.0.9:11434"  # env fallback, no hardcoded IP


def test_paul_off_still_answers_locally():
    # DoD: paul's Ollama unreachable → command falls to the on-device Bielik.
    r = ModelRouter(
        ollama=_Fake("ollama", available=False),
        openai=_Fake("gpt", available=False),
        local_factory=lambda m: _Fake(m),
    )
    assert r.first(Task.COMMAND)[0] == "bielik-1.5b"
    assert r.first(Task.RECOMMEND)[0] == "bielik-4.5b"
