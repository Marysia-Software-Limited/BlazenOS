"""Portable shared-context model for the constellation ("one Jessica").

A :class:`Snapshot` is one node's syncable state — memory (notes / reminders /
voice notes / profile) plus audiobook progress — as plain JSON, tagged with the
node name and a snapshot timestamp. :func:`merge` combines two snapshots
**deterministically**:

- notes / voice notes are immutable → union by ``id``;
- reminders union by ``id``, with ``fired`` OR-ed (fired anywhere = fired);
- profile merges per key, the **newer snapshot** winning conflicts;
- progress is last-writer-wins per book ``slug`` using each entry's ``updated``.

Pure data — no I/O, no imports of any surface. Each node maps its local stores
(``memory.json`` + ``progress.json``) to and from this shape (see the node-side
fabric adapter). Merge is commutative + idempotent, so pulling in any order
converges.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

Item = dict[str, Any]


def _by_id(items: list[Item]) -> dict[str, Item]:
    return {str(i["id"]): i for i in items if i.get("id")}


def _union_by_id(a: list[Item], b: list[Item]) -> list[Item]:
    """Immutable records → union by id, sorted by ``created`` for determinism."""
    merged = {**_by_id(a), **_by_id(b)}
    return sorted(merged.values(), key=lambda i: (str(i.get("created", "")), str(i.get("id"))))


def _merge_reminders(a: list[Item], b: list[Item]) -> list[Item]:
    ma, mb = _by_id(a), _by_id(b)
    out: dict[str, Item] = {}
    for key in ma.keys() | mb.keys():
        x, y = ma.get(key), mb.get(key)
        if x and y:
            merged = dict(x)
            merged["fired"] = bool(x.get("fired")) or bool(y.get("fired"))
            out[key] = merged
        else:
            out[key] = x or y  # type: ignore[assignment]
    return sorted(out.values(), key=lambda i: (str(i.get("created", "")), str(i.get("id"))))


def _merge_profile(a: Item, b: Item, *, a_newer: bool) -> Item:
    # Union of keys; on a shared key, the newer snapshot's value wins.
    return {**b, **a} if a_newer else {**a, **b}


def _merge_progress(a: dict[str, Item], b: dict[str, Item]) -> dict[str, Item]:
    out = dict(a)
    for slug, entry in b.items():
        cur = out.get(slug)
        if cur is None or str(entry.get("updated", "")) > str(cur.get("updated", "")):
            out[slug] = entry
    return out


@dataclass
class Snapshot:
    node: str = ""
    updated: str = ""  # ISO timestamp of this snapshot (drives profile tie-breaks)
    notes: list[Item] = field(default_factory=list)
    reminders: list[Item] = field(default_factory=list)
    voice_notes: list[Item] = field(default_factory=list)
    profile: Item = field(default_factory=dict)
    progress: dict[str, Item] = field(default_factory=dict)  # {slug: {...,"updated"}}

    @classmethod
    def from_stores(cls, *, node: str, updated: str, memory: Item, progress: dict[str, Item]) -> Snapshot:
        """Build from a ``memory.json``-shaped dict + a ``progress.json``-shaped dict."""
        return cls(
            node=node,
            updated=updated,
            notes=list(memory.get("notes", [])),
            reminders=list(memory.get("reminders", [])),
            voice_notes=list(memory.get("voice_notes", [])),
            profile=dict(memory.get("profile", {})),
            progress=dict(progress or {}),
        )

    def memory(self) -> Item:
        """The merged state back in ``memory.json`` shape (profile + item lists)."""
        return {
            "notes": self.notes,
            "reminders": self.reminders,
            "voice_notes": self.voice_notes,
            "profile": self.profile,
        }

    def to_dict(self) -> Item:
        return {
            "node": self.node,
            "updated": self.updated,
            "notes": self.notes,
            "reminders": self.reminders,
            "voice_notes": self.voice_notes,
            "profile": self.profile,
            "progress": self.progress,
        }

    @classmethod
    def from_dict(cls, d: Item) -> Snapshot:
        return cls(
            node=str(d.get("node", "")),
            updated=str(d.get("updated", "")),
            notes=list(d.get("notes", [])),
            reminders=list(d.get("reminders", [])),
            voice_notes=list(d.get("voice_notes", [])),
            profile=dict(d.get("profile", {})),
            progress=dict(d.get("progress", {})),
        )


def merge(a: Snapshot, b: Snapshot) -> Snapshot:
    """Deterministically combine two snapshots (commutative + idempotent)."""
    a_newer = a.updated >= b.updated
    return Snapshot(
        node=a.node if a_newer else b.node,
        updated=max(a.updated, b.updated),
        notes=_union_by_id(a.notes, b.notes),
        reminders=_merge_reminders(a.reminders, b.reminders),
        voice_notes=_union_by_id(a.voice_notes, b.voice_notes),
        profile=_merge_profile(a.profile, b.profile, a_newer=a_newer),
        progress=_merge_progress(a.progress, b.progress),
    )
