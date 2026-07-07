"""Unit tests for the GPU fleet manager (no network / systemctl / GPU)."""
from __future__ import annotations

from mesh_registry import Mesh

from jessica_linux.fleet import control, services, status


def _mesh() -> Mesh:
    return Mesh({"nodes": {"paul": {"host": "h", "resources": {
        "llm": {"ollama-11b": {"kind": "openai", "url": "http://p:11434", "unit": "ollama.service"}},
        "tts": {"xtts": {"kind": "xtts", "url": "http://p:8091/synthesize", "unit": "blazen-xtts.service"}},
        # whisper has NO unit → advertised but not fleet-managed here
        "asr": {"whisper-remote": {"kind": "faster-whisper", "url": "http://p:8090/transcribe"}},
    }}}}, self_node="paul")


class _R:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.returncode = returncode


class _Resp:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_services_are_only_self_node_units():
    units = {s.unit for s in services(_mesh())}
    assert units == {"ollama.service", "blazen-xtts.service"}  # whisper (no unit) excluded


def test_status_reports_active_reachable_and_gpu():
    def opener(url, timeout=0):
        return _Resp()

    def runner(cmd, **kw):
        if cmd[0] == "systemctl":
            return _R(stdout="active\n")
        if cmd[0] == "nvidia-smi":
            return _R(stdout="22842, 24576\n")
        return _R()

    st = status(mesh=_mesh(), opener=opener, runner=runner)
    assert st["healthy"] is True
    assert st["gpu"] == {"used_mib": 22842, "total_mib": 24576}
    assert all(s["ok"] for s in st["services"])


def test_status_flags_a_down_service():
    def opener(url, timeout=0):
        if "8091" in url:
            raise OSError("connection refused")  # xtts endpoint down
        return _Resp()

    def runner(cmd, **kw):
        if cmd[0] == "systemctl":
            return _R(stdout="failed\n" if "xtts" in cmd[-1] else "active\n")
        return _R(stdout="1,2")

    st = status(mesh=_mesh(), opener=opener, runner=runner)
    assert st["healthy"] is False
    xtts = next(s for s in st["services"] if s["name"] == "xtts")
    assert xtts["ok"] is False and xtts["reachable"] is False and xtts["active"] is False
    # a peer reading this routes around xtts; the LLM (ollama) stays healthy
    ollama = next(s for s in st["services"] if s["name"] == "ollama-11b")
    assert ollama["ok"] is True


def test_control_uses_sudo_systemctl():
    calls = {}

    def runner(cmd, **kw):
        calls["cmd"] = cmd
        return _R(returncode=0)

    rc = control("restart", "blazen-xtts.service", runner=runner)
    assert rc == 0
    assert calls["cmd"] == ["sudo", "systemctl", "restart", "blazen-xtts.service"]
