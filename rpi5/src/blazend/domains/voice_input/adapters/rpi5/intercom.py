"""Intercom call leg (2026-09-22): full-duplex voice between two Jessica nodes.

One *leg* runs on each node. It streams the node's mic — read from the shared
audio ring `blazend-audio-in` already fills, so the capture device is never
touched — to ``stdout``, and plays whatever arrives on ``stdin`` through the
node's speaker (``aplay -D jabra_out``: the resample-safe PCM; raw ``plughw``
plays 16 kHz 3× fast on the SPEAK 410). Two legs cross-wired over one SSH
connection (stdin ↔ stdout) make a call — the tailnet is the transport and
the encryption, no new ports, no daemon. The Jabra's hardware AEC breaks the
speaker→mic loop on both ends.

Run by ``scripts/call.py``; direct use::

    python -m blazend.domains.voice_input.adapters.rpi5.intercom [--seconds 120]

While a leg runs it holds the ``speaking`` marker, so wake/ASR ignore the
remote voice coming out of the speaker. The marker is ALWAYS removed on exit
(a stale one deafens the pipeline — 2026-08-09); ``--seconds`` (default 120)
bounds the call so an abandoned session cannot leave the node deaf.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import subprocess
import sys
import threading
import time
import uuid
from typing import IO, BinaryIO

from blazend.domains.voice_input.adapters.rpi5.audio import RingReader
from blazend.ipc import runtime_dir

Stream = IO[bytes] | BinaryIO

_CHUNK_MS = 40


def pump_ring(ring: RingReader, out: Stream, stop: threading.Event,
              *, seconds: float, chunk_ms: int = _CHUNK_MS) -> None:
    """Follow the ring's write position, copying new mic frames to ``out``
    until ``stop`` is set, ``out`` breaks (peer hung up) or ``seconds`` pass."""
    frames = max(1, int(ring.sample_rate * chunk_ms / 1000))
    pos = ring.write_pos
    deadline = time.monotonic() + seconds
    while not stop.is_set() and time.monotonic() < deadline:
        new = ring.write_pos
        if new - pos >= frames:
            try:
                out.write(ring.read_range(pos, new).tobytes())
                out.flush()
            except (BrokenPipeError, OSError):
                break
            pos = new
        else:
            time.sleep(chunk_ms / 2000)
    stop.set()


def _pump_playback(inp: Stream, player_stdin: Stream,
                   stop: threading.Event) -> None:
    """Copy the peer's voice from ``inp`` into the player until EOF/hang-up."""
    while not stop.is_set():
        try:
            data = inp.read(4096)
        except (OSError, ValueError):  # ValueError: stream closed at hang-up
            break
        if not data:
            break
        try:
            player_stdin.write(data)
            player_stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            break
    stop.set()


def _open_player(cmd: list[str], *, retry_s: float = 15.0) -> subprocess.Popen[bytes]:
    """Start aplay, retrying while the device is busy — right after the call
    announcement blazend-audio-out still holds the Jabra for a few seconds
    (half-duplex manager); it drops the device once idle."""
    deadline = time.monotonic() + retry_s
    while True:
        player = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL)
        time.sleep(0.5)
        if player.poll() is None:
            return player
        if time.monotonic() >= deadline:
            raise RuntimeError(f"playback device busy for {retry_s:.0f}s: {cmd}")
        time.sleep(1.0)


def run_leg(*, seconds: float, playback_device: str = "jabra_out") -> int:
    rt = runtime_dir()
    ring = RingReader(rt / "audio-ring.shm")
    marker = rt / "speaking"
    player = _open_player(
        ["aplay", "-q", "-D", playback_device, "-f", "S16_LE",
         "-r", str(ring.sample_rate), "-c", str(ring.channels or 1), "-t", "raw"])
    assert player.stdin is not None
    stop = threading.Event()
    marker.touch()
    try:
        t = threading.Thread(
            target=_pump_playback, args=(sys.stdin.buffer, player.stdin, stop),
            daemon=True)
        t.start()
        pump_ring(ring, sys.stdout.buffer, stop, seconds=seconds)
    finally:
        # A stale `speaking` marker deafens wake+ASR (2026-08-09) — remove it
        # no matter how the call ends.
        marker.unlink(missing_ok=True)
        stop.set()
        with contextlib.suppress(OSError):
            player.stdin.close()
        player.terminate()
        ring.close()
    return 0


def ring_wait(*, from_label: str, timeout_s: float) -> int:
    """Callee-side ring helper, run over SSH by the CALLER. Drops a ring
    request into the callee's ``<runtime>/call/`` spool (the orchestrator
    announces it and opens the voice answer window) and waits for the
    orchestrator's answer file. Prints ``ACCEPT`` / ``REJECT`` / ``TIMEOUT``
    (or ``NOSPOOL`` — the peer's orchestrator predates the call spool)."""
    spool = runtime_dir() / "call"
    if not spool.is_dir():
        print("NOSPOOL", flush=True)
        return 1
    call_id = uuid.uuid4().hex[:10]
    tmp = spool / f"ring-{call_id}.json.tmp"  # .tmp: pathlib globs match dotfiles
    tmp.write_text(json.dumps({"id": call_id, "from": from_label},
                              ensure_ascii=False), encoding="utf-8")
    tmp.rename(spool / f"ring-{call_id}.json")
    answer = spool / f"answer-{call_id}.json"
    deadline = time.monotonic() + timeout_s + 10.0
    verdict = "TIMEOUT"
    try:
        while time.monotonic() < deadline:
            if answer.is_file():
                try:
                    verdict = str(json.loads(answer.read_text(encoding="utf-8"))
                                  .get("answer", "TIMEOUT")).upper()
                except ValueError:
                    verdict = "TIMEOUT"
                break
            time.sleep(0.5)
    finally:
        answer.unlink(missing_ok=True)
        (spool / f"ring-{call_id}.json").unlink(missing_ok=True)
    print(verdict, flush=True)
    return 0


def dial(*, host: str, seconds: float, remote_sudo: bool,
         remote_python: str, remote_env: str) -> int:
    """Caller-side call core (no announcements — the caller's orchestrator or
    scripts/call.py speaks around it): local leg inline, remote leg over one
    SSH connection, streams cross-wired."""
    sudo = "sudo -n " if remote_sudo else ""
    leg_cmd = (f"{sudo}env {remote_env} {remote_python} -m "
               f"blazend.domains.voice_input.adapters.rpi5.intercom "
               f"--seconds {seconds:.0f}")
    ssh = subprocess.Popen(
        ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
         host, leg_cmd],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    assert ssh.stdin is not None and ssh.stdout is not None
    rt = runtime_dir()
    ring = RingReader(rt / "audio-ring.shm")
    marker = rt / "speaking"
    player = _open_player(
        ["aplay", "-q", "-D", "jabra_out", "-f", "S16_LE",
         "-r", str(ring.sample_rate), "-c", "1", "-t", "raw"])
    assert player.stdin is not None
    stop = threading.Event()
    marker.touch()
    try:
        t = threading.Thread(target=_pump_playback,
                             args=(ssh.stdout, player.stdin, stop), daemon=True)
        t.start()
        pump_ring(ring, ssh.stdin, stop, seconds=seconds)
    finally:
        marker.unlink(missing_ok=True)  # stale marker = deaf pipeline
        stop.set()
        for proc in (ssh, player):
            if proc.stdin:
                with contextlib.suppress(OSError):
                    proc.stdin.close()
            proc.terminate()
        ring.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Jessica intercom call leg")
    ap.add_argument("--seconds", type=float, default=120.0,
                    help="hard call cap (an abandoned call must not stay open)")
    ap.add_argument("--playback-device", default="jabra_out")
    ap.add_argument("--ring-from", default="",
                    help="callee helper mode: announce a ring from this label "
                         "and wait for the voice answer")
    ap.add_argument("--timeout", type=float, default=30.0,
                    help="ring mode: how long the answer window stays open")
    ap.add_argument("--dial", default="",
                    help="caller mode: SSH host of the peer to call")
    ap.add_argument("--remote-sudo", action="store_true")
    ap.add_argument("--remote-python", default="/usr/lib/blazen/.venv/bin/python")
    ap.add_argument("--remote-env",
                    default="PYTHONPATH=/usr/lib/blazen BLAZEN_RUNTIME_DIR=/run/blazen")
    args = ap.parse_args()
    if args.ring_from:
        return ring_wait(from_label=args.ring_from, timeout_s=args.timeout)
    if args.dial:
        return dial(host=args.dial, seconds=args.seconds,
                    remote_sudo=args.remote_sudo,
                    remote_python=args.remote_python, remote_env=args.remote_env)
    return run_leg(seconds=args.seconds, playback_device=args.playback_device)


if __name__ == "__main__":
    raise SystemExit(main())
