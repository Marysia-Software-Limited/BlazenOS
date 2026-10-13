"""The Jabra SPEAK 410 hardware stage stays pinned under a softvol (2026-10-12).

On a desktop install the user's volume is the 'JessicaVol' softvol; the
speakerphone's own 'PCM' control must be a fixed 100 % reference, yet the device
keeps dropping it to 3/11 (-24 dB) on its own. Every volume touch re-pins it.
On the appliance (PCM *is* the user's volume) nothing extra is issued.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from blazend.domains.systems.adapters.rpi5.orchestrator import supervisor

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def orch(tmp_path, monkeypatch):
    monkeypatch.setenv("BLAZEN_CONFIG_ROOT", str(REPO / "configs"))
    return supervisor.Orchestrator(peers=("wake",), runtime_dir_=tmp_path)


def _capture(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(supervisor.subprocess, "run",
                        lambda cmd, **kw: calls.append(list(cmd)))
    return calls


def test_softvol_profile_repins_hardware_stage(orch, monkeypatch):
    monkeypatch.setattr(supervisor, "_JABRA_CARD", "USB")
    monkeypatch.setattr(supervisor, "_JABRA_MIXER", "JessicaVol")
    calls = _capture(monkeypatch)
    orch._amixer("50%")
    assert calls == [
        ["amixer", "-c", "USB", "set", "PCM", "100%", "unmute"],
        ["amixer", "-c", "USB", "set", "JessicaVol", "50%"],
    ]


def test_appliance_profile_touches_only_pcm(orch, monkeypatch):
    monkeypatch.setattr(supervisor, "_JABRA_CARD", "USB")
    monkeypatch.setattr(supervisor, "_JABRA_MIXER", "PCM")
    calls = _capture(monkeypatch)
    orch._amixer("60%")
    assert calls == [["amixer", "-c", "USB", "set", "PCM", "60%"]]
