"""Unit tests for node-side fabric sync (no network — injected opener)."""
from __future__ import annotations

import json

from context_sync import Snapshot
from mesh_registry import Mesh

from jessica_linux.fabric import pull_and_merge


def _mesh_two() -> Mesh:
    data = {
        "nodes": {
            "paul": {"host": "p", "resources": {
                "fabric": {"snapshot": {"kind": "fabric", "url": "http://paul:7475/fabric/snapshot"}}}},
            "jessica": {"host": "j", "resources": {
                "fabric": {"snapshot": {"kind": "fabric", "url": "http://jessica:7475/fabric/snapshot"}}}},
        }
    }
    return Mesh(data, self_node="paul")


class _Resp:
    def __init__(self, body: bytes) -> None:
        self._b = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self) -> bytes:
        return self._b


def test_pull_merges_peer_note_and_preserves_unknown_keys(tmp_path):
    mem, prog = tmp_path / "memory.json", tmp_path / "progress.json"
    mem.write_text(json.dumps({"notes": [], "reminders": [], "profile": {}, "seq": 3}))

    peer = Snapshot(node="jessica", updated="2026-07-07T09:00",
                    notes=[{"id": "n1", "text": "kod bramy 4729", "created": "2026-07-07T09:00"}])

    def opener(url, timeout=0):
        assert "jessica" in url  # pulled the peer, never self (paul)
        return _Resp(json.dumps(peer.to_dict()).encode("utf-8"))

    out = pull_and_merge(node="paul", memory_path=mem, progress_path=prog, mesh=_mesh_two(), opener=opener)
    assert out["pulled"] == ["jessica"] and out["notes"] == 1

    saved = json.loads(mem.read_text())
    assert saved["notes"][0]["text"] == "kod bramy 4729"  # peer's note recalled locally
    assert saved["seq"] == 3  # unknown keys preserved through the merge


def test_offline_peer_is_skipped_and_local_survives(tmp_path):
    mem, prog = tmp_path / "memory.json", tmp_path / "progress.json"
    mem.write_text(json.dumps({"notes": [{"id": "a", "text": "moja notatka", "created": "c"}]}))

    def opener(url, timeout=0):
        raise OSError("connection refused")  # peer down

    out = pull_and_merge(node="paul", memory_path=mem, progress_path=prog, mesh=_mesh_two(), opener=opener)
    assert out["pulled"] == []  # strict-improvement: down peer skipped, never fatal
    assert json.loads(mem.read_text())["notes"][0]["id"] == "a"
