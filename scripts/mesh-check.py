#!/usr/bin/env python3
"""Mesh membership + reachability diagnostic (P0 DoD).

Prints this node's identity, every node's advertised resources across the
constellation, and probes THIS node's own resource endpoints for reachability —
proving discovery works end-to-end. Runs from any node; pure stdlib + the shared
``mesh_registry`` domain lib.

    BLAZEN_NODE=paul scripts/mesh-check.py

Reachability is a plain HTTP GET on the advertised URL: any HTTP response (incl.
405/404 from a POST-only endpoint) means the port is serving; only a connection
error / timeout counts as down. The mesh registry itself is pure data — probing
is the caller's job (see domains/mesh-registry).
"""
from __future__ import annotations

import sys
import urllib.error
import urllib.request

from mesh_registry import Mesh

_CATEGORIES = ("llm", "asr", "tts")


def probe(url: str) -> str:
    """Liveness of a resource URL: reachable (with status) or down."""
    try:
        with urllib.request.urlopen(url, timeout=4) as r:  # noqa: S310 (LAN, fixed hosts)
            return f"reachable (HTTP {r.status})"
    except urllib.error.HTTPError as e:
        return f"reachable (HTTP {e.code})"  # 405/404 = server is alive
    except Exception as e:  # noqa: BLE001 — URLError / timeout / socket error = down
        return f"UNREACHABLE ({type(e).__name__})"


def main() -> int:
    mesh = Mesh.load()
    me = mesh.self_node
    print(f"self_node : {me or '(unset — export BLAZEN_NODE)'}")
    print(f"nodes     : {', '.join(mesh.nodes)}")
    print()

    reached_all = True
    for category in _CATEGORIES:
        for res in mesh.resources(category):
            mine = res.node == me
            marker = " <- me" if mine else ""
            where = res.url or "(local)"
            line = f"[{category:3}] {res.node:8} {res.name:16} kind={res.kind:14} {where}{marker}"
            if mine and res.url:
                status = probe(res.url)
                line += f"  {status}"
                reached_all = reached_all and status.startswith("reachable")
            print(line)

    if not me:
        print("\nFAIL: BLAZEN_NODE not set — this node has no identity in the mesh.", file=sys.stderr)
        return 2
    if me not in mesh.nodes:
        print(f"\nFAIL: self_node {me!r} is not a node in the registry.", file=sys.stderr)
        return 2
    if not reached_all:
        print(f"\nWARN: not all of {me}'s advertised endpoints are reachable.", file=sys.stderr)
        return 1
    print(f"\nOK: {me} sees the constellation and reaches all its own endpoints.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
