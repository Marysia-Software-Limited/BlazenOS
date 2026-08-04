"""Unit tests for node-side fabric sync (no network — injected opener)."""
from __future__ import annotations

import json

import pytest
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


# -- voice-memo wav mirroring (2026-08-04) -------------------------------------
def _wav_bytes() -> bytes:
    import io
    import wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16_000)
        w.writeframes(b"\x00\x00" * 1600)
    return buf.getvalue()


def test_pull_mirrors_missing_memo_wav(tmp_path):
    """A memo recorded on the peer becomes playable here: the snapshot brings
    the metadata, the wav lands in voice_notes/synced/<id>.wav, and
    memory.json keeps the ORIGIN path (merge-stable)."""
    import threading

    from jessica_linux.fabric import make_server, synced_wav_dir

    # Peer node ("jessica") with one recorded memo. Both "nodes" share this
    # host's filesystem, so the origin audio_path must NOT resolve locally
    # (production peers are distinct machines) — the peer holds the wav in its
    # own synced mirror, which also exercises the relay branch of _serve_wav.
    peer_dir = tmp_path / "peer"
    peer_dir.mkdir()
    wav = peer_dir / "voice_notes" / "synced" / "vn-7.wav"
    wav.parent.mkdir(parents=True)
    wav.write_bytes(_wav_bytes())
    (peer_dir / "memory.json").write_text(json.dumps({
        "voice_notes": [{"id": "vn-7", "audio_path": "/on/the/origin/node/memo-1.wav",
                         "created": "2026-08-04T10:00", "transcript": "kod do bramy",
                         "duration_s": 0.1, "kind": "voice_note_created"}]}))
    (peer_dir / "progress.json").write_text("{}")
    server = make_server(node="jessica", memory_path=peer_dir / "memory.json",
                         progress_path=peer_dir / "progress.json", host="127.0.0.1", port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    # Local node ("paul") pulls over real HTTP.
    local_dir = tmp_path / "local"
    local_dir.mkdir()
    mem, prog = local_dir / "memory.json", local_dir / "progress.json"
    mem.write_text("{}")
    mesh = Mesh({"nodes": {
        "paul": {"host": "p", "resources": {}},
        "jessica": {"host": "j", "resources": {"fabric": {"snapshot": {
            "kind": "fabric", "url": f"http://127.0.0.1:{port}/fabric/snapshot"}}}},
    }}, self_node="paul")
    out = pull_and_merge(node="paul", memory_path=mem, progress_path=prog, mesh=mesh)
    server.shutdown()

    assert out["pulled"] == ["jessica"] and out["wavs"] == 1
    mirror = synced_wav_dir(mem) / "vn-7.wav"
    assert mirror.exists() and mirror.read_bytes() == _wav_bytes()
    saved = json.loads(mem.read_text())
    # Origin path preserved — the mirror is a lookup-by-id fallback, not a rewrite.
    assert saved["voice_notes"][0]["audio_path"] == "/on/the/origin/node/memo-1.wav"

    # Idempotent: a second pull fetches nothing new.
    server2 = make_server(node="jessica", memory_path=peer_dir / "memory.json",
                          progress_path=peer_dir / "progress.json", host="127.0.0.1", port=0)
    threading.Thread(target=server2.serve_forever, daemon=True).start()
    port2 = server2.server_address[1]
    mesh2 = Mesh({"nodes": {
        "paul": {"host": "p", "resources": {}},
        "jessica": {"host": "j", "resources": {"fabric": {"snapshot": {
            "kind": "fabric", "url": f"http://127.0.0.1:{port2}/fabric/snapshot"}}}},
    }}, self_node="paul")
    out2 = pull_and_merge(node="paul", memory_path=mem, progress_path=prog, mesh=mesh2)
    server2.shutdown()
    assert out2["wavs"] == 0


def test_wav_endpoint_404s_for_unknown_id(tmp_path):
    import threading
    import urllib.error
    import urllib.request

    from jessica_linux.fabric import make_server

    (tmp_path / "memory.json").write_text("{}")
    (tmp_path / "progress.json").write_text("{}")
    server = make_server(node="jessica", memory_path=tmp_path / "memory.json",
                         progress_path=tmp_path / "progress.json", host="127.0.0.1", port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    try:
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(f"http://127.0.0.1:{port}/fabric/voice_note/nope", timeout=5)
    finally:
        server.shutdown()
