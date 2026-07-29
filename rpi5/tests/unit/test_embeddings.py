"""Tier 0 — on-device embedder + semantic note store (no model download).

The real ONNX model is never loaded here: the :class:`Embedder` takes an
injected fake backend, and the :class:`MemoryStore` cosine search is exercised
with synthetic vectors. Mirrors the fake-backend pattern in test_assistant.py.
"""
from __future__ import annotations

import importlib.machinery
import json
import math
import sys
import types
from datetime import datetime

import numpy as np
import pytest

from blazend.config import Config
from blazend.domains.context.adapters.rpi5.embeddings import Embedder, EmbedderError, _OnnxBackend
from blazend.domains.context.adapters.rpi5.memory import MemoryStore

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


# ---------------------------------------------------------------------------
# _OnnxBackend + Embedder real-path coverage, with fake ORT + tokenizers.
# ---------------------------------------------------------------------------


class _FakeEncoding:
    def __init__(self, ids, mask) -> None:
        self.ids = ids
        self.attention_mask = mask


class _FakeTokenizer:
    last_truncation: int | None = None

    @classmethod
    def from_file(cls, _path):  # noqa: ANN001, ANN206
        return cls()

    def enable_truncation(self, *, max_length: int) -> None:
        _FakeTokenizer.last_truncation = max_length

    def encode_batch(self, texts):  # noqa: ANN001
        out = []
        for i, _t in enumerate(texts):
            n = i + 1
            out.append(_FakeEncoding(list(range(1, n + 1)), [1] * n))
        return out


class _FakeInput:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeSession:
    def __init__(self, _model, *, providers) -> None:  # noqa: ANN001
        self.providers = providers

    def get_inputs(self):
        return [_FakeInput("input_ids"), _FakeInput("attention_mask")]

    def run(self, _outputs, feeds):  # noqa: ANN001
        b, t = feeds["input_ids"].shape
        # (B, T, H=3) of ones → mean-pool then L2-normalise downstream.
        return [np.ones((b, t, 3), dtype=np.float32)]


@pytest.fixture
def _fake_onnx_deps(monkeypatch):
    ort = types.ModuleType("onnxruntime")
    ort.InferenceSession = _FakeSession
    ort.__spec__ = importlib.machinery.ModuleSpec("onnxruntime", loader=None)
    tok = types.ModuleType("tokenizers")
    tok.Tokenizer = _FakeTokenizer
    tok.__spec__ = importlib.machinery.ModuleSpec("tokenizers", loader=None)
    monkeypatch.setitem(sys.modules, "onnxruntime", ort)
    monkeypatch.setitem(sys.modules, "tokenizers", tok)


def test_onnx_backend_encode_pools_and_normalises(_fake_onnx_deps, tmp_path):
    model = tmp_path / "model.onnx"
    tokenizer = tmp_path / "tokenizer.json"
    model.write_bytes(b"\x00")
    tokenizer.write_text("{}", encoding="utf-8")
    backend = _OnnxBackend(model, tokenizer, max_seq=32)
    assert _FakeTokenizer.last_truncation == 32

    vecs = backend.encode(["a", "bb"])
    assert len(vecs) == 2
    for v in vecs:
        assert len(v) == 3
        assert abs(sum(x * x for x in v) - 1.0) < 1e-5  # L2-normalised


def test_embedder_real_path_available_and_embeds(_fake_onnx_deps, tmp_path, monkeypatch):
    """available True (deps + files present) → embed builds the ONNX backend."""
    monkeypatch.setenv("BLAZEN_MODELS_DIR", str(tmp_path))
    root = tmp_path / "embeddings" / "m"
    root.mkdir(parents=True)
    (root / "model.onnx").write_bytes(b"\x00")
    (root / "tokenizer.json").write_text("{}", encoding="utf-8")

    emb = Embedder(
        config=_cfg(
            {
                "active_model": "m",
                "e5_prefixes": {"query": "query: ", "passage": "passage: "},
                "models": {
                    "m": {
                        "max_seq": 16,
                        "files": [{"file": "model.onnx"}, {"file": "tokenizer.json"}],
                    }
                },
            }
        )
    )
    assert emb.available is True
    out = emb.embed(["notatka"], kind="passage")
    assert len(out) == 1 and len(out[0]) == 3


def test_embed_empty_returns_empty():
    emb = Embedder(config=_cfg({"active_model": "m", "models": {"m": {}}}), backend=_FakeBackend())
    assert emb.embed([]) == []


def test_ensure_backend_raises_without_configured_model():
    emb = Embedder(config=_cfg({"active_model": "", "models": {}}))
    with pytest.raises(EmbedderError, match="no active embedding model"):
        emb.embed(["x"])


def test_ensure_backend_raises_when_files_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("BLAZEN_MODELS_DIR", str(tmp_path))  # files absent
    emb = Embedder(
        config=_cfg(
            {
                "active_model": "m",
                "models": {"m": {"files": [{"file": "model.onnx"}, {"file": "tokenizer.json"}]}},
            }
        )
    )
    with pytest.raises(EmbedderError, match="not found"):
        emb.embed(["x"])


def test_embed_wraps_backend_runtime_failure():
    class _Boom:
        def encode(self, _texts):  # noqa: ANN001
            raise ValueError("onnx blew up")

    emb = Embedder(config=_cfg({"active_model": "m", "models": {"m": {}}}), backend=_Boom())
    with pytest.raises(EmbedderError, match="embedding failed"):
        emb.embed(["x"])


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


# -- unified memory index: voice memos join the vector store (2026-07-29) ------
def test_memory_items_include_transcribed_voice_notes_only(tmp_path):
    mem = MemoryStore(tmp_path / "m.json")
    n = mem.add_note("hasło do wifi", now=NOW)
    v = mem.add_voice_note(tmp_path / "a.wav", now=NOW, duration_s=3.2,
                           transcript="kupić filtr do wody")
    mem.add_voice_note(tmp_path / "b.wav", now=NOW)  # no transcript → not searchable
    items = mem.memory_items()
    assert {(i.id, i.kind) for i in items} == {(n.id, "note"), (v.id, "voice")}
    voice = next(i for i in items if i.kind == "voice")
    assert voice.text == "kupić filtr do wody" and voice.audio_path.endswith("a.wav")


def test_items_missing_embeddings_covers_both_kinds(tmp_path):
    mem = MemoryStore(tmp_path / "m.json")
    n = mem.add_note("notatka", now=NOW)
    v = mem.add_voice_note(tmp_path / "a.wav", now=NOW, transcript="nagranie")
    missing = {i.id for i in mem.items_missing_embeddings(model="m")}
    assert missing == {n.id, v.id}
    mem.set_note_embedding(v.id, [1.0, 0.0, 0.0], model="m")
    assert {i.id for i in mem.items_missing_embeddings(model="m")} == {n.id}


def test_search_memory_semantic_returns_mixed_scored_hits(tmp_path):
    mem = MemoryStore(tmp_path / "m.json")
    n = mem.add_note("góry i weekend", now=NOW)
    v = mem.add_voice_note(tmp_path / "a.wav", now=NOW, transcript="wyjazd w góry")
    mem.set_note_embedding(n.id, [1.0, 0.0, 0.0], model="m")
    mem.set_note_embedding(v.id, [0.9, 0.1, 0.0], model="m")
    hits = mem.search_memory_semantic([1.0, 0.0, 0.0], limit=4, min_score=0.5,
                                      rel_margin=0.1)
    assert [h.id for h in hits] == [n.id, v.id]  # both, best first
    assert hits[0].score >= hits[1].score > 0.5
    assert hits[1].kind == "voice" and hits[1].audio_path.endswith("a.wav")


def test_search_notes_semantic_still_filters_to_text_notes(tmp_path):
    mem = MemoryStore(tmp_path / "m.json")
    v = mem.add_voice_note(tmp_path / "a.wav", now=NOW, transcript="nagranie o górach")
    mem.set_note_embedding(v.id, [1.0, 0.0, 0.0], model="m")
    assert mem.search_notes_semantic([1.0, 0.0, 0.0], min_score=0.5) == []


def test_store_reloads_when_another_process_writes(tmp_path):
    # Live layout: brain, orchestrator and ASR each hold their own MemoryStore
    # over one file. A memo added through one instance must be visible to the
    # others without a restart (mtime-based revalidation).
    p = tmp_path / "m.json"
    reader = MemoryStore(p)
    assert reader.voice_notes() == []
    writer = MemoryStore(p)
    writer.add_voice_note(tmp_path / "a.wav", now=NOW, transcript="nowe nagranie")
    got = reader.voice_notes()
    assert len(got) == 1 and got[0].transcript == "nowe nagranie"
