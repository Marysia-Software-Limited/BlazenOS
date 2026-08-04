"""Node-side fabric: sync this node's Jessica context with peers over the mesh.

Uses the portable :mod:`context_sync` model. A node can **serve** its snapshot
(so peers pull it) and **pull** peers' snapshots from the mesh's ``fabric``
resources, merging them into the local ``memory.json`` + ``progress.json``.
Read-mostly v1: last-writer-wins, converges regardless of order. The Pi appliance
stays authoritative for itself; the mesh is strict-improvement (an offline peer is
skipped, never fatal).
"""
from __future__ import annotations

import json
import threading
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from context_sync import Snapshot, merge
from mesh_registry import Mesh

# Voice-memo wav mirroring (2026-08-04): snapshot rows carry each memo's
# transcript + the RECORDING node's audio_path. The wav itself is pulled over
# the same fabric port into `voice_notes/synced/<id>.wav` — memory.json stays
# untouched (the by-id merge would resurrect a rewritten audio_path), and
# playback falls back to the mirror via `MemoryStore.voice_note_wav`.
_WAV_FETCH_CAP = 20                  # mirrors fetched per sync pass
_WAV_MAX_BYTES = 8 * 1024 * 1024     # ~4 min of 16 kHz mono — sanity cap


def synced_wav_dir(memory_path: Path) -> Path:
    """Where fabric-pulled memo wavs land, next to this node's memory.json."""
    return memory_path.parent / "voice_notes" / "synced"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def local_snapshot(*, node: str, memory_path: Path, progress_path: Path,
                   now: str | None = None) -> Snapshot:
    """This node's current context as a portable Snapshot."""
    return Snapshot.from_stores(
        node=node,
        updated=now or datetime.now(UTC).isoformat(),
        memory=_read_json(memory_path),
        progress=_read_json(progress_path),
    )


def apply_snapshot(snap: Snapshot, *, memory_path: Path, progress_path: Path) -> None:
    """Write a (merged) snapshot back to the local stores, preserving unknown keys
    (e.g. memory.json's ``seq``)."""
    mem = _read_json(memory_path)
    mem.update(snap.memory())  # notes / reminders / voice_notes / profile
    _write_json(memory_path, mem)
    _write_json(progress_path, snap.progress)


def _fetch_missing_wavs(snap: Snapshot, peer_bases: list[str], memory_path: Path,
                        opener: Callable[..., Any]) -> int:
    """Mirror memo wavs this node lacks from the pulled peers. Best-effort and
    bounded (cap + size guard); a missing wav just means replay stays
    unavailable here until the next sync."""
    fetched = 0
    for row in snap.voice_notes:
        if fetched >= _WAV_FETCH_CAP:
            break
        vid = str(row.get("id", ""))
        audio_path = str(row.get("audio_path", ""))
        if not vid or (audio_path and Path(audio_path).exists()):
            continue  # no id, or this node already holds the recording
        dest = synced_wav_dir(memory_path) / f"{vid}.wav"
        if dest.exists():
            continue
        for base in peer_bases:
            try:
                with opener(f"{base}/fabric/voice_note/{vid}", timeout=15) as r:  # noqa: S310
                    body = r.read()
            except Exception:  # noqa: BLE001 — peer offline / 404 → try the next
                continue
            if not body or len(body) > _WAV_MAX_BYTES:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(".wav.tmp")
            tmp.write_bytes(body)
            tmp.replace(dest)
            fetched += 1
            break
    return fetched


def pull_and_merge(*, node: str, memory_path: Path, progress_path: Path,
                   mesh: Mesh | None = None,
                   opener: Callable[..., Any] = urllib.request.urlopen) -> dict[str, Any]:
    """Pull every peer's snapshot from the mesh's ``fabric`` resources, merge them
    into this node's context, persist, and mirror any memo wavs this node lacks.
    Returns a small summary."""
    mesh = mesh or Mesh.load()
    local = local_snapshot(node=node, memory_path=memory_path, progress_path=progress_path)
    pulled: list[str] = []
    peer_bases: list[str] = []
    for res in mesh.resources("fabric"):
        if res.node == node or not res.url:
            continue
        try:
            with opener(res.url, timeout=8) as r:  # noqa: S310 — our own LAN peers
                peer = Snapshot.from_dict(json.loads(r.read()))
        except Exception:  # noqa: BLE001 — peer offline → skip (strict-improvement)
            continue
        local = merge(local, peer)
        pulled.append(res.node)
        peer_bases.append(res.url.rsplit("/fabric/", 1)[0])
    apply_snapshot(local, memory_path=memory_path, progress_path=progress_path)
    wavs = _fetch_missing_wavs(local, peer_bases, memory_path, opener)
    return {"pulled": pulled, "notes": len(local.notes),
            "reminders": len(local.reminders), "books": len(local.progress),
            "wavs": wavs}


def make_server(*, node: str, memory_path: Path, progress_path: Path,
                host: str = "0.0.0.0", port: int = 7475) -> ThreadingHTTPServer:
    """An HTTP server that serves this node's snapshot at ``GET /fabric/snapshot``
    so peers can pull it. Call ``.serve_forever()`` (or run in a thread)."""

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
            path = self.path.rstrip("/")
            if path.startswith("/fabric/voice_note/"):
                self._serve_wav(path.rsplit("/", 1)[-1])
                return
            if path != "/fabric/snapshot":
                self.send_error(404)
                return
            snap = local_snapshot(node=node, memory_path=memory_path, progress_path=progress_path)
            body = json.dumps(snap.to_dict(), ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _serve_wav(self, vid: str) -> None:
            """Serve a memo's wav BY ID — the path comes from this node's own
            store (never the client), so no traversal. A node that only holds
            a synced mirror relays it, so a third node can still fetch."""
            mem = _read_json(memory_path)
            row = next((v for v in mem.get("voice_notes", [])
                        if str(v.get("id")) == vid), None)
            wav: Path | None = None
            if row is not None:
                p = Path(str(row.get("audio_path", "")))
                if p.exists():
                    wav = p
            if wav is None:
                mirror = synced_wav_dir(memory_path) / f"{vid}.wav"
                if row is not None and mirror.exists():
                    wav = mirror
            if wav is None:
                self.send_error(404)
                return
            body = wav.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a: Any) -> None:  # silence request logging
            pass

    return ThreadingHTTPServer((host, port), _Handler)


def serve(*, node: str, memory_path: Path, progress_path: Path, port: int = 7475) -> None:
    server = make_server(node=node, memory_path=memory_path, progress_path=progress_path, port=port)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        t.join()
    except KeyboardInterrupt:
        server.shutdown()
