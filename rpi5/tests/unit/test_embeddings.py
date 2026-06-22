"""Tier 0 — on-device embedder + semantic note store (no model download).

The real ONNX model is never loaded here: the :class:`Embedder` takes an
injected fake backend, and the :class:`MemoryStore` cosine search is exercised
with synthetic vectors. Mirrors the fake-backend pattern in test_assistant.py.
"""
from __future__ import annotations

import json
import math
from datetime import datetime

from blazend.assistant.embeddings import Embedder
from blazend.assistant.memory import MemoryStore
from blazend.config import Config

NOW = datetime(2026, 6, 12, 14, 0, 0)


class _FakeBackend:
    """Stand-in for _OnnxBackend — records the (prefixed) texts it encodes."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    def encode(self, texts):
        self.seen.extend(texts)
        return [[1.0, 0.0, 0.0] for _ in texts]


def _cfg(data):
    return Config(name="embeddings", data=data, sources=[])


def test_embedder_applies_e5_query_passage_prefixes():
    backend = _FakeBackend()
    emb = Embedder(
        config=_cfg(
            {
                "active_model": "m",
                "e5_prefixes": {"query": "query: ", "passage": "passage: "},
                "models": {"m": {"max_seq": 64}},
            }
        ),
        backend=backend,
    )
    assert emb.available
    emb.embed(["jak się masz"], kind="query")
    emb.embed(["notatka"], kind="passage")
    assert backend.seen == ["query: jak się masz", "passage: notatka"]


def test_embedder_unavailable_without_model_files(tmp_path, monkeypatch):
    monkeypatch.setenv("BLAZEN_MODELS_DIR", str(tmp_path))
    emb = Embedder(
        config=_cfg(
            {
                "active_model": "m",
                "models": {"m": {"files": [{"file": "model.onnx"}, {"file": "tokenizer.json"}]}},
            }
        )
    )
    assert emb.available is False  # files absent → engine degrades to lexical recall


def test_memory_semantic_search_ranks_by_cosine(tmp_path):
    mem = MemoryStore(tmp_path / "m.json")
    a = mem.add_note("góry i weekend", now=NOW, title="weekend")
    b = mem.add_note("hasło do wifi", now=NOW)
    mem.set_note_embedding(a.id, [1.0, 0.0, 0.0], model="m")
    mem.set_note_embedding(b.id, [0.0, 1.0, 0.0], model="m")
    hits = mem.search_notes_semantic([1.0, 0.0, 0.0], limit=2, min_score=0.5)
    assert [h.id for h in hits] == [a.id]  # only the aligned note clears the floor


def test_memory_embeddings_persist_and_invalidate_on_model_change(tmp_path):
    p = tmp_path / "m.json"
    mem = MemoryStore(p)
    n = mem.add_note("coś", now=NOW)
    assert mem.notes_missing_embeddings(model="m") == mem.notes()  # nothing embedded yet
    mem.set_note_embedding(n.id, [0.1, 0.2, 0.3], model="m")
    assert MemoryStore(p).notes_missing_embeddings(model="m") == []  # persisted across reopen
    assert len(MemoryStore(p).notes_missing_embeddings(model="other")) == 1  # model change → stale


def test_semantic_search_empty_store_returns_nothing(tmp_path):
    mem = MemoryStore(tmp_path / "m.json")
    assert mem.search_notes_semantic([1.0, 0.0, 0.0]) == []


def test_semantic_search_honors_limit(tmp_path):
    mem = MemoryStore(tmp_path / "m.json")
    for i in range(5):
        n = mem.add_note(f"note {i}", now=NOW)
        mem.set_note_embedding(n.id, [1.0, 0.0, 0.0], model="m")
    hits = mem.search_notes_semantic([1.0, 0.0, 0.0], limit=2, min_score=0.0)
    assert len(hits) == 2


def test_semantic_search_skips_orphan_vectors(tmp_path):
    # A vector whose note was removed must not crash or surface a ghost hit.
    mem = MemoryStore(tmp_path / "m.json")
    n = mem.add_note("real", now=NOW)
    mem.set_note_embedding(n.id, [1.0, 0.0, 0.0], model="m")
    mem.set_note_embedding("note-999", [1.0, 0.0, 0.0], model="m")  # no such note
    hits = mem.search_notes_semantic([1.0, 0.0, 0.0], limit=5, min_score=0.0)
    assert [h.id for h in hits] == [n.id]


def test_recall_matches_title_and_text(tmp_path):
    mem = MemoryStore(tmp_path / "m.json")
    n = mem.add_note("treść o górach", now=NOW, title="weekend")
    assert [m.id for m in mem.recall("weekend")] == [n.id]  # matched on title
    assert [m.id for m in mem.recall("górach")] == [n.id]  # matched on body


def test_titled_note_round_trips_on_disk(tmp_path):
    p = tmp_path / "m.json"
    MemoryStore(p).add_note("treść notatki", now=NOW, title="tytuł")
    reloaded = MemoryStore(p).notes()[0]
    assert reloaded.title == "tytuł" and reloaded.text == "treść notatki"


def _unit_vec_with_cosine(c: float) -> list[float]:
    """A 2-D unit vector whose cosine to [1, 0] is exactly ``c``."""
    return [c, math.sqrt(max(0.0, 1.0 - c * c))]


def test_semantic_search_relative_margin_isolates_winner(tmp_path):
    # e5-like compressed band: a clear winner (0.90) amid near-ties (0.83, 0.82).
    mem = MemoryStore(tmp_path / "m.json")
    ids = {}
    for name, cos in [("a", 0.90), ("b", 0.83), ("c", 0.82)]:
        n = mem.add_note(name, now=NOW)
        ids[name] = n.id
        mem.set_note_embedding(n.id, _unit_vec_with_cosine(cos), model="m")
    hits = mem.search_notes_semantic(
        _unit_vec_with_cosine(1.0), limit=4, min_score=0.80, rel_margin=0.06
    )
    assert [h.id for h in hits] == [ids["a"]]  # near-ties fall outside the margin


def test_semantic_search_floor_drops_all_when_nothing_relevant(tmp_path):
    # Every match is mediocre (top 0.79 < floor 0.80) → inject nothing.
    mem = MemoryStore(tmp_path / "m.json")
    for name, cos in [("x", 0.79), ("y", 0.76)]:
        n = mem.add_note(name, now=NOW)
        mem.set_note_embedding(n.id, _unit_vec_with_cosine(cos), model="m")
    assert (
        mem.search_notes_semantic(
            _unit_vec_with_cosine(1.0), limit=4, min_score=0.80, rel_margin=0.06
        )
        == []
    )


def test_note_loads_without_title_field(tmp_path):
    # Back-compat: notes written before `title` existed must still load.
    p = tmp_path / "m.json"
    p.write_text(
        json.dumps(
            {
                "notes": [
                    {
                        "id": "note-1",
                        "text": "stara notatka",
                        "created": "2026-01-01T00:00:00",
                        "kind": "note_created",
                    }
                ],
                "seq": 1,
            }
        ),
        encoding="utf-8",
    )
    notes = MemoryStore(p).notes()
    assert len(notes) == 1 and notes[0].title == "" and notes[0].text == "stara notatka"
