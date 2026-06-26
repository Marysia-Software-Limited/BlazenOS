"""Ports for the systems domain.

The systems domain is the platform the body runs on — process supervision
(``orchestrator.supervisor``), lifecycle (``bootstrap``, ``recovery``, ``state``),
status LEDs, and the watchdog (``blazend-health``, Rust). Its seams are the host
itself: **systemd units** and the **IPC contract** (``system.event``, ``error``,
the health socket), not in-process Python interfaces. There is therefore no
Python port to promote in Phase 1; the contract is the schema in
``configs/_schema/events/``. See docs/19-DOMAIN-ARCHITECTURE.md.
"""

from __future__ import annotations

__all__: list[str] = []
