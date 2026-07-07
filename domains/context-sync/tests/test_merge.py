"""Tests for the portable shared-context merge (one Jessica across nodes)."""
from __future__ import annotations

from context_sync import Snapshot, merge


def _snap(node, updated, *, notes=None, reminders=None, profile=None, progress=None):
    return Snapshot(
        node=node,
        updated=updated,
        notes=notes or [],
        reminders=reminders or [],
        profile=profile or {},
        progress=progress or {},
    )


def test_notes_union_by_id_and_sorted():
    a = _snap("paul", "2026-07-07T10:00", notes=[{"id": "n1", "text": "brama 4729", "created": "2026-07-07T09:00"}])
    b = _snap("jessica", "2026-07-07T11:00", notes=[{"id": "n2", "text": "mleko", "created": "2026-07-07T10:00"}])
    m = merge(a, b)
    ids = [n["id"] for n in m.notes]
    assert ids == ["n1", "n2"]  # union, sorted by created
    # merging a duplicate id is idempotent (no dupes)
    assert merge(m, a).notes == m.notes


def test_note_saved_on_one_node_is_present_after_merge_both_directions():
    pi = _snap("jessica", "2026-07-07T09:00", notes=[{"id": "n1", "text": "kod bramy 4729", "created": "2026-07-07T09:00"}])
    paul = _snap("paul", "2026-07-07T09:30")
    # paul pulls the Pi's snapshot → recalls the note
    assert any(n["text"] == "kod bramy 4729" for n in merge(paul, pi).notes)
    # and the reverse order converges to the same set
    assert {n["id"] for n in merge(paul, pi).notes} == {n["id"] for n in merge(pi, paul).notes}


def test_profile_conflict_resolves_to_newer_snapshot():
    older = _snap("paul", "2026-07-07T09:00", profile={"name": "Paweł", "city": "Kraków"})
    newer = _snap("jessica", "2026-07-07T12:00", profile={"name": "Pawel"})
    m = merge(older, newer)
    assert m.profile["name"] == "Pawel"     # newer wins the shared key
    assert m.profile["city"] == "Kraków"    # non-conflicting key retained


def test_reminders_merge_fired_state():
    a = _snap("paul", "2026-07-07T10:00", reminders=[{"id": "r1", "text": "pranie", "created": "x", "fired": False}])
    b = _snap("jessica", "2026-07-07T10:05", reminders=[{"id": "r1", "text": "pranie", "created": "x", "fired": True}])
    m = merge(a, b)
    assert len(m.reminders) == 1 and m.reminders[0]["fired"] is True


def test_progress_is_last_writer_wins_per_slug():
    a = _snap("paul", "2026-07-07T10:00", progress={"znachor": {"chapter": 2, "offset_s": 30, "updated": "2026-07-07T10:00"}})
    b = _snap("jessica", "2026-07-07T09:00", progress={"znachor": {"chapter": 5, "offset_s": 12, "updated": "2026-07-07T12:00"}})
    m = merge(a, b)
    assert m.progress["znachor"]["chapter"] == 5  # b's entry has the later `updated`
    # symmetric regardless of merge order
    assert merge(b, a).progress["znachor"]["chapter"] == 5


def test_roundtrip_dict():
    s = _snap("paul", "2026-07-07T10:00", notes=[{"id": "n1", "text": "x", "created": "c"}], profile={"name": "P"})
    assert Snapshot.from_dict(s.to_dict()) == s
