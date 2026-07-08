"""context_sync — portable shared-context model for the constellation.

`Snapshot` + `merge` let every node (jessica / paul / rachel) converge to the same
Jessica context — memory + audiobook progress — regardless of sync order.
"""
from __future__ import annotations

from context_sync.paths import context_paths, data_dir
from context_sync.snapshot import Snapshot, merge

__all__ = ["Snapshot", "context_paths", "data_dir", "merge"]
