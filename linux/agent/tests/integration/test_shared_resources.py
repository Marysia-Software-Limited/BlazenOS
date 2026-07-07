"""Constellation integration tests — verify this node can reach every SHARED
resource advertised in the mesh (LLM / ASR / TTS / fabric / health / media).

These hit the live LAN, so they are OPT-IN: skipped unless ``BLAZEN_INTEGRATION=1``
(the offline ``make test-fast`` never runs them). Run on EACH node to check that
node's access to the constellation:

    BLAZEN_INTEGRATION=1 BLAZEN_NODE=paul make test-integration

Policy: **self-strict, peer-lenient**. A resource this node OWNS must be up (a node
serves what it advertises) — that's a hard failure. A PEER resource that's simply
offline (a Mac that's asleep) is reported and skipped, not failed; but a peer that's
up yet returns a broken response fails. Functional checks (Ollama answers, XTTS
renders, media serves the catalog, fabric snapshots, fleet health) assert on the
response when the endpoint is reachable.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

import pytest
from mesh_registry import Mesh

pytestmark = pytest.mark.skipif(
    not os.environ.get("BLAZEN_INTEGRATION"),
    reason="live-mesh integration test — set BLAZEN_INTEGRATION=1 to run",
)

_TIMEOUT = 10


def _mesh() -> Mesh:
    return Mesh.load()


def _get(url: str, *, data: bytes | None = None):
    req = urllib.request.Request(url, data=data,  # noqa: S310 — our own LAN services
                                 headers={"Content-Type": "application/json"} if data else {})
    return urllib.request.urlopen(req, timeout=_TIMEOUT)  # noqa: S310


def _reachable(url: str) -> tuple[bool, str]:
    try:
        with _get(url) as r:
            return True, f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        return True, f"HTTP {e.code}"  # 405/404 from a POST-only endpoint = alive
    except Exception as e:  # noqa: BLE001
        return False, type(e).__name__


def _require_reachable(res) -> None:
    """Fail if THIS node's own resource is down; skip if a peer is simply offline."""
    ok, how = _reachable(res.url)
    if ok:
        return
    me = _mesh().self_node
    if res.node == me:
        pytest.fail(f"this node ({me}) does not serve its own {res.category}/{res.name} "
                    f"({res.url}): {how}")
    pytest.skip(f"peer {res.node} offline — {res.category}/{res.name} ({res.url}) unreachable")


# --- reachability across every advertised resource --------------------------

@pytest.mark.parametrize("category", ["llm", "asr", "tts", "fabric", "health", "media"])
def test_networked_resources_reachable(category):
    mesh = _mesh()
    me = mesh.self_node
    networked = [r for r in mesh.resources(category) if r.url]
    if not networked:
        pytest.skip(f"no networked {category} resource in the mesh")
    down_self, down_peer, up = [], [], []
    for res in networked:
        ok, how = _reachable(res.url)
        tag = f"{res.name}@{res.node}"
        (up if ok else (down_self if res.node == me else down_peer)).append(tag if ok else f"{tag}({how})")
    print(f"  [{category}] up={up} peers_offline={down_peer}")
    assert not down_self, f"this node ({me}) is not serving its own {category}: {down_self}"
    assert up, f"no {category} resource reachable at all (self + peers all down)"


# --- functional checks per shared service -----------------------------------

def test_llm_ollama_answers():
    res = _mesh().resource("llm", "ollama-11b")
    if not (res and res.url):
        pytest.skip("no ollama-11b in the mesh")
    _require_reachable(res)
    body = json.dumps({"model": os.environ.get("BLAZEN_LLM_OLLAMA_MODEL",
                       "SpeakLeash/bielik-11b-v2.3-instruct:Q8_0"),
                       "messages": [{"role": "user", "content": "Odpowiedz jednym słowem: tak."}],
                       "stream": False}).encode()
    with _get(res.url.rstrip("/") + "/api/chat", data=body) as r:
        out = json.loads(r.read())
    assert out.get("message", {}).get("content"), "Ollama returned no content"


def test_tts_xtts_renders_wav():
    res = _mesh().resource("tts", "xtts")
    if not (res and res.url):
        pytest.skip("no xtts in the mesh")
    _require_reachable(res)
    body = json.dumps({"text": "Test.", "language": "pl"}).encode()
    last = None
    for attempt in range(3):  # tolerate transient contention with a concurrent render
        try:
            with _get(res.url, data=body) as r:
                wav = r.read()
            assert wav[:4] == b"RIFF" and len(wav) > 1000, "XTTS did not return a WAV"
            return
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (attempt + 1))
    pytest.fail(f"XTTS render failed after retries: {last}")


def test_media_serves_catalog_and_first_chapter():
    res = _mesh().resource("media", "audiobooks")
    if not (res and res.url):
        pytest.skip("no media resource in the mesh")
    _require_reachable(res)
    with _get(res.url.rstrip("/") + "/catalog.json") as r:
        cat = json.loads(r.read())
    books = cat.get("books", [])
    assert books, "media catalog is empty"
    ch1 = books[0]["chapters"][0]
    ok, how = _reachable(ch1)
    assert ok, f"first chapter {ch1} unreachable: {how}"


def test_fabric_snapshots_valid_where_up():
    endpoints = [r for r in _mesh().resources("fabric") if r.url]
    if not endpoints:
        pytest.skip("no fabric endpoints in the mesh")
    reached = 0
    for res in endpoints:
        ok, _ = _reachable(res.url)
        if not ok:
            continue  # peer may be off
        with _get(res.url) as r:
            snap = json.loads(r.read())
        assert "node" in snap and "notes" in snap, f"{res.node} fabric snapshot malformed"
        reached += 1
    assert reached, "no fabric snapshot endpoint was reachable"


def test_fleet_health_reports_status():
    res = _mesh().resource("health", "fleet")
    if not (res and res.url):
        pytest.skip("no fleet health resource in the mesh")
    _require_reachable(res)
    with _get(res.url) as r:
        health = json.loads(r.read())
    assert "services" in health and "healthy" in health
