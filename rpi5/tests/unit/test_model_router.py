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


# Mesh-style multi-backend routing table for the MECHANICS tests below
# (preference order, skip-unavailable, eviction, probe cache). The repo's real
# llm.yaml routes every task to the on-device bielik-1.5b (node-local processing,
# decision 2026-07-13) — that POLICY is asserted separately in
# test_default_config_routes_everything_local.
_MESH_CFG = {"routing": {
    "single_local_model": True,
    "ollama_probe_ttl_s": 30,
    "tasks": {
        "command":   ["ollama-11b", "bielik-1.5b"],
        "recommend": ["ollama-11b", "bielik-4.5b"],
        "open_qa":   ["gpt-5.5", "ollama-11b", "bielik-4.5b"],
    },
    "backends": {
        "bielik-1.5b": {"model": "bielik-1.5b-v3-instruct-q4_k_m"},
        "bielik-4.5b": {"model": "bielik-4.5b-v3-instruct-q6_k"},
    },
}}


def _router(*, ollama_up: bool, key: bool):
    seen: dict[str, _Fake] = {}
    r = ModelRouter(
        cfg=_MESH_CFG,
        ollama=_Fake("ollama", available=ollama_up),
        openai=_Fake("gpt", available=key),
        local_factory=lambda model: seen.setdefault(model, _Fake(model)),
    )
    return r, seen


def test_default_config_routes_everything_local():
    """POLICY (2026-07-13, node-local processing): with the repo's real llm.yaml,
    every task routes to the on-device bielik-1.5b — even with paul's Ollama UP
    and an OpenAI key present. No LLM hop leaves the Pi."""
    seen: dict[str, _Fake] = {}
    r = ModelRouter(  # no cfg override → loads configs/llm.yaml
        ollama=_Fake("ollama", available=True),
        openai=_Fake("gpt", available=True),
        local_factory=lambda model: seen.setdefault(model, _Fake(model)),
    )
    assert r.first(Task.COMMAND)[0] == "bielik-1.5b"
    assert r.first(Task.RECOMMEND)[0] == "bielik-1.5b"
    assert r.first(Task.OPEN_QA)[0] == "bielik-1.5b"


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
        cfg=_MESH_CFG,
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
        cfg=_MESH_CFG,
        ollama=_Fake("ollama", available=False),
        openai=_Fake("gpt", available=False),
        local_factory=lambda m: _Fake(m),
    )
    assert r.first(Task.COMMAND)[0] == "bielik-1.5b"
    assert r.first(Task.RECOMMEND)[0] == "bielik-4.5b"


# -- a generic OpenAI-compatible mesh peer (rachel's MLX) -----------------

def _mesh_with_peer(name: str, url: str, model: str):
    from mesh_registry import Mesh
    return Mesh(
        {"nodes": {"rachel": {"host": "192.168.50.186",
                              "resources": {"llm": {name: {"kind": "openai", "url": url, "model": model}}}}}},
        self_node="paul",
    )


def test_mesh_openai_peer_built_from_the_mesh():
    # An unknown backend name that the mesh advertises as kind: openai builds a
    # generic client from the resource's URL + model tag (no hardcoded adapter).
    r = ModelRouter(mesh=_mesh_with_peer("mlx-qwen72b", "http://192.168.50.186:11436", "Qwen72"))
    backend = r._build("mlx-qwen72b")
    assert backend is not None
    assert backend.url == "http://192.168.50.186:11436"
    assert backend.model == "Qwen72"


def test_unknown_backend_absent_from_mesh_is_dropped():
    # Not a known name and not in the mesh → unbuildable → skipped, never crashes.
    r = ModelRouter(mesh=_mesh_with_peer("mlx-qwen72b", "http://x:11436", "Q"))
    assert r._build("some-typo-backend") is None


def _peer_router(*, peer_up: bool):
    """RECOMMEND routed rachel-first, with rachel's peer availability controllable
    and everything else down — so we see rachel win, then the local fallback."""
    cfg = {"routing": {
        "tasks": {"recommend": ["mlx-qwen72b", "ollama-11b", "bielik-4.5b"]},
        "backends": {"bielik-4.5b": {"model": "b45"}},
    }}
    return ModelRouter(
        cfg=cfg,
        mesh=_mesh_with_peer("mlx-qwen72b", "http://192.168.50.186:11436", "Q"),
        backends={"mlx-qwen72b": _Fake("mlx-qwen72b", available=peer_up)},
        ollama=_Fake("ollama", available=False),
        openai=_Fake("gpt", available=False),
        local_factory=lambda m: _Fake(m),
    )


def test_recommend_prefers_rachel_peer_when_up():
    assert _peer_router(peer_up=True).first(Task.RECOMMEND)[0] == "mlx-qwen72b"


def test_recommend_falls_back_to_local_when_peer_off():
    # DoD: rachel off → strict-improvement, falls through to the on-device Bielik.
    assert _peer_router(peer_up=False).first(Task.RECOMMEND)[0] == "bielik-4.5b"
