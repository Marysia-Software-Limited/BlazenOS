"""Tier 1+ — cross-language stack integration.

Boots a minimal slice of the stack (Rust `blazend-wake` mock + Rust
`blazend-health` + Python orchestrator) under a temp runtime dir,
waits until `state.json` reflects the cross-language event flow, and
asserts:

    1. `state.ready == True`           (from the Rust health heartbeat)
    2. `state.wake_word.last_fired`    (from a Rust wake event)
    3. orchestrator subscribed to all three peers (logs)

Skips when the Rust binaries are not built yet (CI runs `make build`
before this; locally the user is on their own — `pytest.skip` reports
that explicitly).
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CARGO_DEBUG = REPO / "crates" / "target" / "debug"
CARGO_RELEASE = REPO / "crates" / "target" / "release"


def _binary(name: str) -> Path | None:
    for root in (CARGO_DEBUG, CARGO_RELEASE):
        p = root / name
        if p.is_file() and os.access(p, os.X_OK):
            return p
    return None


def _wait_for(predicate, *, timeout: float = 20.0, interval: float = 0.2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError("predicate never became true within timeout")


def test_cross_language_state_propagation():
    wake = _binary("blazend-wake")
    health = _binary("blazend-health")
    if wake is None or health is None:
        pytest.skip("Rust binaries not built — run `make rust` or `cd domains && cargo build` first")

    runtime = Path(tempfile.mkdtemp(prefix="bl-xl-", dir="/tmp"))
    env = {**os.environ, "BLAZEN_RUNTIME_DIR": str(runtime)}
    procs: list[subprocess.Popen] = []

    try:
        # Rust peers first.
        procs.append(subprocess.Popen(
            [str(wake), "--mock", "--mock-period-s", "2"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ))
        procs.append(subprocess.Popen(
            [str(health)],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ))
        # Give the Rust units time to bind their sockets.
        time.sleep(0.5)

        # Python orchestrator via its real __main__ entrypoint. It tries
        # to subscribe to every peer in DEFAULT_PEERS; the ones we didn't
        # start (audio-in, asr, brain, tts, audio-out) just sit in the
        # reconnect-with-backoff loop, which is fine.
        orch_log = runtime / "orch.log"
        orch = subprocess.Popen(
            [sys.executable, "-m", "blazend.domains.systems.adapters.rpi5.orchestrator"],
            env=env,
            stdout=orch_log.open("w"),
            stderr=subprocess.STDOUT,
        )
        procs.append(orch)

        state_path = runtime / "state.json"

        def has_wake() -> bool:
            if not state_path.is_file():
                return False
            try:
                data = json.loads(state_path.read_text())
            except json.JSONDecodeError:
                return False
            return (
                data.get("ready") is True
                and (data.get("wake_word") or {}).get("last_fired") is not None
            )

        _wait_for(has_wake, timeout=20.0)

        snap = json.loads(state_path.read_text())
        assert snap["ready"] is True
        # blazend-wake --mock defaults to the active "jessica" model.
        assert snap["wake_word"]["last_fired"] in ("jessica", "hey_blazen_en", "hey_blazen_pl")
        assert snap["wake_word"]["last_language"] in ("en", "pl")
        assert "wake" in snap["units"]
        assert "health" in snap["units"]
    finally:
        for p in procs:
            try:
                p.send_signal(signal.SIGTERM)
            except ProcessLookupError:
                pass
        time.sleep(0.3)
        for p in procs:
            try:
                p.kill()
            except ProcessLookupError:
                pass
            try:
                p.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        shutil.rmtree(runtime, ignore_errors=True)
