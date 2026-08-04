"""Persistent memory: terms/notes the user asks to remember, and reminders.

Stored as a flat JSON log of fabric-shaped facts (``note_created`` /
``reminder_created``) so it can later be promoted onto the Rust
`blazend-fabric` SyncLog and synced across devices. For the prototype it is a
single local JSON file; reminders carry an ISO ``due`` and a ``fired`` flag.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np


def data_dir() -> Path:
    """Where the prototype persists memory. Override with ``BLAZEN_DATA_DIR``."""
    root = os.environ.get("BLAZEN_DATA_DIR")
    if root:
        return Path(root)
    runtime = os.environ.get("BLAZEN_RUNTIME_DIR", f"/tmp/blazen-{os.getuid()}")
    return Path(runtime) / "data"


@dataclass
class Note:
    """A remembered term/fact.

    ``title`` is an optional short label for longer dictated notes
    ("zapamiętaj: <title>. <content>"); when empty the note is a single
    untitled blob (the original behaviour) and ``text`` holds the whole thing.
    """

    id: str
    text: str
    created: str  # ISO timestamp
    title: str = ""
    kind: str = "note_created"


@dataclass
class Reminder:
    """A time-bound reminder (also covers alarms and events via ``category``)."""

    id: str
    text: str
    due: str  # ISO timestamp
    created: str  # ISO timestamp
    fired: bool = False
    category: str = "reminder"  # reminder | alarm | event
    kind: str = "reminder_created"


@dataclass
class VoiceNote:
    """A recorded audio memo (the wav lives next to memory.json).

    ``title`` is the short spoken label from the dictation dialog ("Jak
    zatytułować notatkę?"); empty for one-shot memos and claimed clips."""

    id: str
    audio_path: str
    created: str  # ISO timestamp
    duration_s: float = 0.0
    transcript: str = ""
    title: str = ""
    kind: str = "voice_note_created"


@dataclass
class MemoryItem:
    """A memory normalized for the unified semantic index: a text ``Note`` or a
    ``VoiceNote`` with a transcript. ``audio_path`` is empty for text notes;
    ``score`` is filled by :meth:`MemoryStore.search_memory_semantic`."""

    id: str
    kind: str  # "note" | "voice"
    text: str
    title: str = ""
    audio_path: str = ""
    score: float = 0.0


@dataclass
class _Db:
    notes: list[dict[str, Any]] = field(default_factory=list)
    reminders: list[dict[str, Any]] = field(default_factory=list)
    voice_notes: list[dict[str, Any]] = field(default_factory=list)
    profile: dict[str, Any] = field(default_factory=dict)
    seq: int = 0


class MemoryStore:
    """Notes + reminders persisted to a JSON file.

    Three live processes hold their own instance over the same file (brain,
    orchestrator, ASR), so every read path revalidates against the file's
    mtime and every mutation reloads first — last-writer-wins on the whole
    file, which is fine for a single user's memory but would lose one of two
    truly simultaneous writes (accepted; the fabric SyncLog is the real fix).
    """

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else data_dir() / "memory.json"
        self._mtime = self._stat_ns(self.path)
        self._db = self._load()
        # Note embeddings live in a sidecar so memory.json stays human-readable;
        # loaded lazily on first semantic call.
        self._emb_cache: dict[str, Any] | None = None
        self._emb_mtime = -1

    # -- persistence -------------------------------------------------------
    @staticmethod
    def _stat_ns(path: Path) -> int:
        try:
            return path.stat().st_mtime_ns
        except OSError:
            return -1

    def _load(self) -> _Db:
        if self.path.exists():
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return _Db(
                notes=raw.get("notes", []),
                reminders=raw.get("reminders", []),
                voice_notes=raw.get("voice_notes", []),
                profile=raw.get("profile", {}),
                seq=raw.get("seq", 0),
            )
        return _Db()

    def _maybe_reload(self) -> None:
        """Pick up another process's writes (mtime changed → reload)."""
        m = self._stat_ns(self.path)
        if m != self._mtime:
            self._mtime = m
            self._db = self._load()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(self._db), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)
        self._mtime = self._stat_ns(self.path)

    def _next_id(self, prefix: str) -> str:
        self._db.seq += 1
        return f"{prefix}-{self._db.seq}"

    # -- notes -------------------------------------------------------------
    def add_note(self, text: str, *, now: datetime, title: str = "") -> Note:
        self._maybe_reload()
        note = Note(
            id=self._next_id("note"),
            text=text.strip(),
            created=now.isoformat(),
            title=title.strip(),
        )
        self._db.notes.append(asdict(note))
        self._save()
        return note

    def notes(self) -> list[Note]:
        self._maybe_reload()
        return [Note(**n) for n in self._db.notes]

    def recall(self, query: str | None = None) -> list[Note]:
        notes = self.notes()
        if not query:
            return notes
        q = query.casefold()
        return [n for n in notes if q in n.text.casefold() or q in n.title.casefold()]

    # -- unified memory view (notes + transcribed voice memos) --------------
    def memory_items(self) -> list[MemoryItem]:
        """Every searchable memory, normalized: text notes plus voice memos
        that have a transcript (an untranscribed wav has nothing to embed)."""
        self._maybe_reload()
        items = [
            MemoryItem(id=n.id, kind="note", text=n.text, title=n.title)
            for n in self.notes()
        ]
        items += [
            MemoryItem(id=v.id, kind="voice", text=v.transcript, title=v.title,
                       audio_path=v.audio_path)
            for v in self.voice_notes()
            if v.transcript
        ]
        return items

    # -- memory embeddings (on-device semantic recall) ----------------------
    def _emb_path(self) -> Path:
        # Historical name kept for device continuity — it now holds vectors for
        # BOTH text notes (note-N) and voice memos (vn-N); ids share one seq.
        return self.path.parent / "note_embeddings.json"

    def _load_embeddings(self) -> dict[str, Any]:
        p = self._emb_path()
        m = self._stat_ns(p)
        if self._emb_cache is None or m != self._emb_mtime:
            self._emb_mtime = m
            if p.exists():
                self._emb_cache = json.loads(p.read_text(encoding="utf-8"))
            else:
                self._emb_cache = {"model": "", "vectors": {}}
        return self._emb_cache

    def _save_embeddings(self) -> None:
        emb = self._load_embeddings()
        p = self._emb_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(emb, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
        self._emb_mtime = self._stat_ns(p)

    def set_note_embedding(self, note_id: str, vector: Sequence[float], *, model: str = "") -> None:
        """Persist a note's embedding. A change of ``model`` drops stale vectors."""
        emb = self._load_embeddings()
        if model and emb.get("model") != model:
            emb["model"] = model
            emb["vectors"] = {}
        emb["vectors"][note_id] = [float(x) for x in vector]
        self._save_embeddings()

    def items_missing_embeddings(self, *, model: str = "") -> list[MemoryItem]:
        """Memories with no stored vector (or all of them if the model changed)."""
        emb = self._load_embeddings()
        if model and emb.get("model") != model:
            return self.memory_items()
        have = emb.get("vectors", {})
        return [it for it in self.memory_items() if it.id not in have]

    def notes_missing_embeddings(self, *, model: str = "") -> list[Note]:
        """Back-compat: text notes only. Prefer :meth:`items_missing_embeddings`."""
        ids = {it.id for it in self.items_missing_embeddings(model=model) if it.kind == "note"}
        return [n for n in self.notes() if n.id in ids]

    def search_memory_semantic(
        self,
        query_vec: Sequence[float],
        *,
        limit: int = 4,
        min_score: float = 0.0,
        rel_margin: float = 0.0,
    ) -> list[MemoryItem]:
        """Top memories (text notes + voice-memo transcripts) by cosine
        similarity to ``query_vec`` (descending); ``score`` is set on each hit.

        Filtering is calibrated for embedders (e.g. e5) whose cosines are
        compressed into a narrow high band, where a flat absolute threshold
        can't separate relevant from irrelevant:

        * ``min_score`` — the **best** match must clear this absolute floor,
          else nothing is returned (handles "no relevant memory": every score
          is mediocre). It is also the hard floor for every returned item.
        * ``rel_margin`` — keep only items within this cosine margin of the top
          hit, which isolates a clear winner from a cluster of near-ties.
        """
        emb = self._load_embeddings()
        vectors: dict[str, list[float]] = emb.get("vectors", {})
        if not vectors:
            return []
        q = np.asarray(query_vec, dtype=np.float32)
        qn = float(np.linalg.norm(q))
        if qn == 0.0:
            return []
        q = q / qn
        by_id = {it.id: it for it in self.memory_items()}
        scored: list[tuple[float, MemoryItem]] = []
        for iid, vec in vectors.items():
            item = by_id.get(iid)
            if item is None:
                continue
            v = np.asarray(vec, dtype=np.float32)
            vn = float(np.linalg.norm(v))
            if vn == 0.0:
                continue
            scored.append((float(np.dot(q, v / vn)), item))
        scored.sort(key=lambda t: t[0], reverse=True)
        if not scored or scored[0][0] < min_score:
            return []
        cutoff = max(min_score, scored[0][0] - rel_margin)
        hits = []
        for s, item in scored:
            if s >= cutoff:
                item.score = s
                hits.append(item)
        return hits[:limit]

    def search_notes_semantic(
        self,
        query_vec: Sequence[float],
        *,
        limit: int = 4,
        min_score: float = 0.0,
        rel_margin: float = 0.0,
    ) -> list[Note]:
        """Back-compat: text-note hits only. Prefer :meth:`search_memory_semantic`."""
        hits = self.search_memory_semantic(
            query_vec, limit=limit, min_score=min_score, rel_margin=rel_margin)
        by_id = {n.id: n for n in self.notes()}
        return [by_id[h.id] for h in hits if h.kind == "note" and h.id in by_id]

    # -- profile (user facts: name, …) -------------------------------------
    def set_profile(self, key: str, value: str, *, now: datetime) -> None:
        """Store a user fact (e.g. ``name``). Last write wins; persisted."""
        self._maybe_reload()
        self._db.profile[key] = {"value": value.strip(), "set": now.isoformat(), "kind": "profile_set"}
        self._save()

    def get_profile(self, key: str, default: str | None = None) -> str | None:
        """Read a stored user fact, or ``default`` if unset."""
        self._maybe_reload()
        entry = self._db.profile.get(key)
        if isinstance(entry, dict):
            return str(entry.get("value", default)) if entry.get("value") is not None else default
        return default

    # -- voice notes -------------------------------------------------------
    def voice_notes_dir(self) -> Path:
        """Directory holding recorded voice-note wavs (next to memory.json)."""
        d = self.path.parent / "voice_notes"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def add_voice_note(
        self, audio_path: Path | str, *, now: datetime, duration_s: float = 0.0,
        transcript: str = "", title: str = ""
    ) -> VoiceNote:
        self._maybe_reload()
        vn = VoiceNote(
            id=self._next_id("vn"),
            audio_path=str(audio_path),
            created=now.isoformat(),
            duration_s=round(float(duration_s), 1),
            transcript=transcript.strip(),
            title=title.strip(),
        )
        self._db.voice_notes.append(asdict(vn))
        self._save()
        return vn

    def voice_notes(self) -> list[VoiceNote]:
        self._maybe_reload()
        return [VoiceNote(**v) for v in self._db.voice_notes]

    def voice_note_wav(self, note_id: str, audio_path: str = "") -> Path | None:
        """The playable wav for a voice note: its own recording when present
        (on the node that captured it), else the fabric-synced mirror
        ``voice_notes/synced/<id>.wav`` pulled from a peer. None → no audio
        here (transcript-only memory)."""
        if audio_path:
            p = Path(audio_path)
            if p.exists():
                return p
        mirror = self.voice_notes_dir() / "synced" / f"{note_id}.wav"
        return mirror if mirror.exists() else None

    def delete_last_memory(self) -> MemoryItem | None:
        """Remove the most recently created memory (note or voice memo) — the
        spoken "usuń ostatnią notatkę". The row and its vector go away; a
        voice memo's wav is MOVED to ``<data>/trash/`` rather than deleted, so
        a slip of the tongue can be undone by hand. Returns what was removed
        (spoken back as the confirmation), or None when the store is empty."""
        self._maybe_reload()
        candidates: list[tuple[str, str, dict[str, Any]]] = [
            (str(n.get("created", "")), "note", n) for n in self._db.notes
        ] + [
            (str(v.get("created", "")), "voice", v) for v in self._db.voice_notes
        ]
        if not candidates:
            return None
        _, kind, row = max(candidates, key=lambda t: t[0])
        if kind == "note":
            self._db.notes.remove(row)
            item = MemoryItem(id=str(row["id"]), kind="note",
                              text=str(row.get("text", "")), title=str(row.get("title", "")))
        else:
            self._db.voice_notes.remove(row)
            item = MemoryItem(id=str(row["id"]), kind="voice",
                              text=str(row.get("transcript", "")),
                              audio_path=str(row.get("audio_path", "")))
            # Trash whichever audio this node holds — the original recording
            # and/or a fabric-synced mirror of it.
            trash = self.path.parent / "trash"
            for src in (Path(item.audio_path),
                        self.voice_notes_dir() / "synced" / f"{item.id}.wav"):
                if src.exists():
                    try:
                        trash.mkdir(parents=True, exist_ok=True)
                        src.rename(trash / src.name)
                    except OSError:
                        pass
        self._save()
        emb = self._load_embeddings()
        if item.id in emb.get("vectors", {}):
            emb["vectors"].pop(item.id, None)
            self._save_embeddings()
        return item

    def claim_last_clip(
        self, utterance_text: str, *, max_age_s: float = 15.0
    ) -> tuple[str, float] | None:
        """Claim the ASR's rolling clip of the CURRENT utterance so a spoken
        memory keeps its own recording ("zapamiętaj, że …" → sound + text).

        blazend-asr drops every transcribed window into ``<data>/clips/`` and
        points ``last.json`` at it (a file handshake — the closed ``asr.final``
        schema stays untouched). The claim only succeeds when the clip is
        fresh and its transcript contains ``utterance_text`` (folded), so a
        later utterance can never be attached to the wrong memory. On success
        the wav is MOVED into :meth:`voice_notes_dir` and ``(path,
        duration_s)`` is returned."""
        marker = self.path.parent / "clips" / "last.json"
        try:
            raw = json.loads(marker.read_text(encoding="utf-8"))
            recorded = datetime.fromisoformat(str(raw.get("ts", "")))
        except (OSError, ValueError):
            return None
        if (datetime.now(UTC) - recorded).total_seconds() > max_age_s:
            return None

        def _fold(s: str) -> str:
            return re.sub(r"[\W_]+", " ", s.casefold()).strip()

        if _fold(utterance_text) not in _fold(str(raw.get("text", ""))):
            return None
        src = Path(str(raw.get("path", "")))
        if not src.exists():
            return None
        dest = self.voice_notes_dir() / src.name
        try:
            src.rename(dest)  # same filesystem (both under the data dir)
            marker.unlink(missing_ok=True)
        except OSError:
            return None
        return str(dest), float(raw.get("duration_s", 0.0) or 0.0)

    # -- reminders ---------------------------------------------------------
    def add_reminder(
        self, text: str, *, due: datetime, now: datetime, category: str = "reminder"
    ) -> Reminder:
        self._maybe_reload()
        rem = Reminder(
            id=self._next_id("rem"),
            text=text.strip(),
            due=due.isoformat(),
            created=now.isoformat(),
            category=category,
        )
        self._db.reminders.append(asdict(rem))
        self._save()
        return rem

    def pending(self) -> list[Reminder]:
        self._maybe_reload()
        return [Reminder(**r) for r in self._db.reminders if not r.get("fired")]

    def due(self, now: datetime) -> list[Reminder]:
        """Return + mark fired every pending reminder whose time has come."""
        self._maybe_reload()
        fired: list[Reminder] = []
        changed = False
        for r in self._db.reminders:
            if r.get("fired"):
                continue
            if datetime.fromisoformat(r["due"]) <= now:
                r["fired"] = True
                changed = True
                fired.append(Reminder(**r))
        if changed:
            self._save()
        return fired
