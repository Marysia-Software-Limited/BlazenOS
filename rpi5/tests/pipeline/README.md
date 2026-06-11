# Tier 2 — pipeline integration (QEMU)

Boots the blazen_os image in QEMU and asserts on basic system invariants
(SSH reachable, every `blazend-*` unit running, no restart loops in the
first 5 minutes). See [`../../docs/08-TESTING.md`](../../docs/08-TESTING.md)
§"Tier 2".

Tests here are pytest files that talk to the running VM over SSH.
Populated in M1 (`test_boot.py`).
