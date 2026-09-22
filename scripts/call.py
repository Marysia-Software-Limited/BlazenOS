#!/usr/bin/env python3
"""Call between two Jessica nodes (2026-09-22): full-duplex intercom.

Wires this machine's intercom leg to a remote node's leg over one SSH
connection (stdin ↔ stdout, tailnet-encrypted, no new ports). Each leg reads
its node's mic from the shared audio ring (the capture device stays with
blazend-audio-in) and plays the peer through ``jabra_out``; the Jabra's
hardware AEC breaks the speaker→mic loop. Both ends first ANNOUNCE the call
through the normal TTS path (announce spool), then hold the ``speaking``
marker for the duration so wake/ASR ignore the remote voice.

Usage:  scripts/call.py jessica-ts [--seconds 120]

MVP limits (documented in docs/04): started and ended from the CLI (or the
--seconds cap) — not yet by voice; while the call runs both nodes are
deliberately deaf to wake words.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "rpi5" / "src"))

REMOTE_PY = "/usr/lib/blazen/.venv/bin/python"
REMOTE_ENV = "PYTHONPATH=/usr/lib/blazen BLAZEN_RUNTIME_DIR=/run/blazen"


def _local_runtime() -> Path:
    return Path(os.environ.get("BLAZEN_RUNTIME_DIR")
                or f"/run/user/{os.getuid()}/blazen")


def _load_audio_env(rt: Path) -> None:
    """Export the node's audio env (ALSA_CONFIG_PATH defines jabra_out)."""
    env_file = rt / "audio.env"
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())


def _announce_local(rt: Path, text: str) -> None:
    spool = rt / "announce"
    spool.mkdir(parents=True, exist_ok=True)
    name = f"call-{int(time.time())}-{uuid.uuid4().hex[:6]}.json"
    tmp = spool / f"{name}.tmp"
    tmp.write_text(json.dumps({"text": text, "lang": "pl"}, ensure_ascii=False),
                   encoding="utf-8")
    tmp.rename(spool / name)


def _announce_remote(host: str, text: str) -> None:
    payload = json.dumps({"text": text, "lang": "pl"}, ensure_ascii=False)
    name = f"call-{int(time.time())}-{uuid.uuid4().hex[:6]}.json"
    script = (f"cat > /run/blazen/announce/{name}.tmp && "
              f"mv /run/blazen/announce/{name}.tmp /run/blazen/announce/{name}")
    subprocess.run(["ssh", "-o", "BatchMode=yes", host,
                    f"sudo -n bash -c {shlex.quote(script)}"],
                   input=payload.encode(), check=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Call another Jessica node")
    ap.add_argument("host", help="SSH host of the peer node (e.g. jessica-ts)")
    ap.add_argument("--seconds", type=float, default=120.0,
                    help="hard call cap — both legs hang up after this")
    args = ap.parse_args()

    rt = _local_runtime()
    _load_audio_env(rt)
    from blazend.domains.voice_input.adapters.rpi5.intercom import (
        _open_player, _pump_playback, pump_ring)
    from blazend.domains.voice_input.adapters.rpi5.audio import RingReader

    print("announcing the call on both ends…")
    _announce_local(rt, "Łączę rozmowę z drugą Jessiką. Mów po sygnale.")
    _announce_remote(args.host, "Połączenie głosowe od Jessiki z paula. Słuchaj i mów swobodnie.")
    time.sleep(7)  # let both TTS announcements play out before we go deaf

    leg_cmd = (f"sudo -n env {REMOTE_ENV} {REMOTE_PY} -m "
               f"blazend.domains.voice_input.adapters.rpi5.intercom "
               f"--seconds {args.seconds:.0f}")
    ssh = subprocess.Popen(
        ["ssh", "-o", "BatchMode=yes", args.host, leg_cmd],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    assert ssh.stdin is not None and ssh.stdout is not None

    ring = RingReader(rt / "audio-ring.shm")
    marker = rt / "speaking"
    player = _open_player(
        ["aplay", "-q", "-D", "jabra_out", "-f", "S16_LE",
         "-r", str(ring.sample_rate), "-c", "1", "-t", "raw"])
    assert player.stdin is not None
    stop = threading.Event()
    marker.touch()
    print(f"call up (cap {args.seconds:.0f}s) — Ctrl-C to hang up")
    try:
        t = threading.Thread(target=_pump_playback,
                             args=(ssh.stdout, player.stdin, stop), daemon=True)
        t.start()
        pump_ring(ring, ssh.stdin, stop, seconds=args.seconds)
    except KeyboardInterrupt:
        pass
    finally:
        marker.unlink(missing_ok=True)  # stale marker = deaf pipeline
        stop.set()
        for p in (ssh, player):
            if p.stdin:
                try:
                    p.stdin.close()
                except OSError:
                    pass
            p.terminate()
        ring.close()
        _announce_local(rt, "Rozmowa zakończona.")
        try:
            _announce_remote(args.host, "Rozmowa zakończona.")
        except subprocess.CalledProcessError:
            pass
    print("hung up")
    return 0


if __name__ == "__main__":
    sys.exit(main())
