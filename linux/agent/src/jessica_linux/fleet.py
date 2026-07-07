"""paul manages its GPU service fleet — status, control, and a health endpoint.

A node's manageable services are its own mesh resources that carry a ``unit:``
(systemd unit). On paul: ``ollama.service`` (:11434), ``blazen-whisper.service``
(:8090), ``blazen-xtts.service`` (:8091). ``status()`` reports each service's HTTP
reachability + ``systemctl is-active`` + GPU VRAM (``nvidia-smi``); ``control()``
drives systemctl; ``serve()`` exposes ``GET /fleet/health`` so peers see one
liveness view. Routing already skips unreachable backends (P3) — this adds the
lifecycle + an aggregated health surface for ops and mesh liveness.

Pure adapters are injected (``opener`` / ``runner``) so tests need no network,
systemctl, or GPU.
"""
from __future__ import annotations

import json
import subprocess
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from mesh_registry import Mesh

_CATEGORIES = ("llm", "asr", "tts", "fabric", "health")
_ACTIONS = ("start", "stop", "restart")


@dataclass
class Service:
    name: str
    category: str
    unit: str
    url: str | None


def services(mesh: Mesh, node: str | None = None) -> list[Service]:
    """This node's manageable services (its mesh resources carrying a ``unit``)."""
    node = node or mesh.self_node
    out: list[Service] = []
    for category in _CATEGORIES:
        for r in mesh.resources(category):
            unit = r.attrs.get("unit") if r.attrs else None
            if r.node == node and unit:
                out.append(Service(name=r.name, category=category, unit=str(unit), url=r.url))
    return out


def _reachable(url: str | None, opener: Callable[..., Any]) -> bool:
    if not url:
        return False
    try:
        with opener(url, timeout=4) as resp:  # noqa: S310 — our own LAN services
            return bool(200 <= resp.status < 600)
    except urllib.error.HTTPError:
        return True  # any HTTP response (405/404 from a POST-only endpoint) = alive
    except Exception:  # noqa: BLE001 — connection error / timeout = down
        return False


def _is_active(unit: str, runner: Callable[..., Any]) -> bool:
    try:
        r = runner(["systemctl", "is-active", unit], capture_output=True, text=True)
        return str(getattr(r, "stdout", "")).strip() == "active"
    except Exception:  # noqa: BLE001
        return False


def _vram(runner: Callable[..., Any]) -> dict[str, int] | None:
    try:
        r = runner(["nvidia-smi", "--query-gpu=memory.used,memory.total",
                    "--format=csv,noheader,nounits"], capture_output=True, text=True)
        used, total = (int(x) for x in str(r.stdout).strip().split(","))
        return {"used_mib": used, "total_mib": total}
    except Exception:  # noqa: BLE001 — no GPU / no nvidia-smi
        return None


def status(*, mesh: Mesh | None = None, node: str | None = None,
           opener: Callable[..., Any] = urllib.request.urlopen,
           runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    """Aggregate fleet health: per-service (active + reachable) + GPU VRAM."""
    mesh = mesh or Mesh.load()
    node = node or mesh.self_node
    svcs: list[dict[str, Any]] = []
    for s in services(mesh, node):
        active = _is_active(s.unit, runner)
        reachable = _reachable(s.url, opener)
        svcs.append({"name": s.name, "category": s.category, "unit": s.unit,
                     "active": active, "reachable": reachable, "ok": active and reachable})
    return {"node": node, "gpu": _vram(runner), "services": svcs,
            "healthy": all(s["ok"] for s in svcs) if svcs else True}


def control(action: str, unit: str, *, runner: Callable[..., Any] = subprocess.run) -> int:
    """start / stop / restart one unit via systemctl (sudo). Returns the exit code."""
    if action not in _ACTIONS:
        raise ValueError(f"bad fleet action {action!r}")
    r = runner(["sudo", "systemctl", action, unit], capture_output=True, text=True)
    return int(getattr(r, "returncode", 1))


def control_all(action: str, *, mesh: Mesh | None = None, node: str | None = None,
                runner: Callable[..., Any] = subprocess.run) -> dict[str, int]:
    mesh = mesh or Mesh.load()
    return {s.unit: control(action, s.unit, runner=runner) for s in services(mesh, node)}


def make_server(*, mesh: Mesh | None = None, node: str | None = None, port: int = 7476,
                host: str = "0.0.0.0") -> ThreadingHTTPServer:
    """HTTP server exposing ``GET /fleet/health`` → the aggregated status JSON."""
    resolved = mesh or Mesh.load()

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path.rstrip("/") != "/fleet/health":
                self.send_error(404)
                return
            body = json.dumps(status(mesh=resolved, node=node)).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a: Any) -> None:
            pass

    return ThreadingHTTPServer((host, port), _Handler)


def cli(action: str, *, node: str | None = None) -> int:
    """`jessica --fleet <action>` — status / start / stop / restart / verify / serve."""
    if action in _ACTIONS:
        for unit, rc in control_all(action, node=node).items():
            print(f"{action} {unit}: {'ok' if rc == 0 else f'FAILED (rc={rc})'}")
        return 0
    if action == "serve":
        srv = make_server(node=node)
        print("serving fleet health on :7476/fleet/health (Ctrl-C stops)")
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            t.join()
        except KeyboardInterrupt:
            srv.shutdown()
        return 0

    # status / verify
    st = status(node=node)
    gpu = st["gpu"]
    if gpu:
        print(f"GPU: {gpu['used_mib']}/{gpu['total_mib']} MiB")
    for s in st["services"]:
        mark = "✓" if s["ok"] else "✗"
        print(f"  {mark} {s['name']:16} {s['unit']:24} active={s['active']} reachable={s['reachable']}")
    print(f"fleet healthy: {st['healthy']}")
    return 0 if (st["healthy"] or action != "verify") else 1
