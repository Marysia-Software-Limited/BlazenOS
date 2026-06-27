"""Persistent memory: terms/notes the user asks to remember, and reminders.

Stored as a flat JSON log of fabric-shaped facts (``note_created`` /
``reminder_created``) so it can later be promoted onto the Rust
`blazend-fabric` SyncLog and synced across devices. For the prototype it is a
single local JSON file; reminders carry an ISO ``due`` and a ``fired`` flag.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
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
    """A recorded audio memo (the wav lives next to memory.json)."""

    id: str
    audio_path: str
    created: str  # ISO timestamp
    duration_s: float = 0.0
    transcript: str = ""
    kind: str = "voice_note_created"


@dataclass
class _Db:
    notes: list[dict[str, Any]] = field(default_factory=list)
    reminders: list[dict[str, Any]] = field(default_factory=list)
    voice_notes: list[dict[str, Any]] = field(default_factory=list)
    profile: dict[str, Any] = field(default_factory=dict)
    seq: int = 0


class MemoryStore:
    """Notes + reminders persisted to a JSON file."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else data_dir() / "memory.json"
        self._db = self._load()
        # Note embeddings live in a sidecar so memory.json stays human-readable;
        # loaded lazily on first semantic call.
        self._emb_cache: dict[str, Any] | None = None

    # -- persistence -------------------------------------------------------
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

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(self._db), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def _next_id(self, prefix: str) -> str:
        self._db.seq += 1
        return f"{prefix}-{self._db.seq}"

    # -- notes -------------------------------------------------------------
    def add_note(self, text: str, *, now: datetime, title: str = "") -> Note:
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
        return [Note(**n) for n in self._db.notes]

    def recall(self, query: str | None = None) -> list[Note]:
        notes = self.notes()
        if not query:
            return notes
        q = query.casefold()
        return [n for n in notes if q in n.text.casefold() or q in n.title.casefold()]

    # -- note embeddings (on-device semantic recall) -----------------------
    def _emb_path(self) -> Path:
        return self.path.parent / "note_embeddings.json"

    def _load_embeddings(self) -> dict[str, Any]:
        if self._emb_cache is None:
            p = self._emb_path()
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

    def set_note_embedding(self, note_id: str, vector: Sequence[float], *, model: str = "") -> None:
        """Persist a note's embedding. A change of ``model`` drops stale vectors."""
        emb = self._load_embeddings()
        if model and emb.get("model") != model:
            emb["model"] = model
            emb["vectors"] = {}
        emb["vectors"][note_id] = [float(x) for x in vector]
        self._save_embeddings()

    def notes_missing_embeddings(self, *, model: str = "") -> list[Note]:
        """Notes with no stored vector (or all of them if the model changed)."""
        emb = self._load_embeddings()
        if model and emb.get("model") != model:
            return self.notes()
        have = emb.get("vectors", {})
        return [n for n in self.notes() if n.id not in have]

    def search_notes_semantic(
        self,
        query_vec: Sequence[float],
        *,
        limit: int = 4,
        min_score: float = 0.0,
        rel_margin: float = 0.0,
    ) -> list[Note]:
        """Top notes by cosine similarity to ``query_vec`` (descending).

        Filtering is calibrated for embedders (e.g. e5) whose cosines are
        compressed into a narrow high band, where a flat absolute threshold
        can't separate relevant from irrelevant:

        * ``min_score`` — the **best** match must clear this absolute floor,
          else nothing is returned (handles "no relevant note": every score is
          mediocre). It is also the hard floor for every returned note.
        * ``rel_margin`` — keep only notes within this cosine margin of the top
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
        by_id = {n.id: n for n in self.notes()}
        scored: list[tuple[float, Note]] = []
        for nid, vec in vectors.items():
            note = by_id.get(nid)
            if note is None:
                continue
            v = np.asarray(vec, dtype=np.float32)
            vn = float(np.linalg.norm(v))
            if vn == 0.0:
                continue
            scored.append((float(np.dot(q, v / vn)), note))
        scored.sort(key=lambda t: t[0], reverse=True)
        if not scored or scored[0][0] < min_score:
            return []
        cutoff = max(min_score, scored[0][0] - rel_margin)
        return [n for s, n in scored if s >= cutoff][:limit]

    # -- profile (user facts: name, …) -------------------------------------
    def set_profile(self, key: str, value: str, *, now: datetime) -> None:
        """Store a user fact (e.g. ``name``). Last write wins; persisted."""
        self._db.profile[key] = {"value": value.strip(), "set": now.isoformat(), "kind": "profile_set"}
        self._save()

    def get_profile(self, key: str, default: str | None = None) -> str | None:
        """Read a stored user fact, or ``default`` if unset."""
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
        self, audio_path: Path | str, *, now: datetime, duration_s: float = 0.0, transcript: str = ""
    ) -> VoiceNote:
        vn = VoiceNote(
            id=self._next_id("vn"),
            audio_path=str(audio_path),
            created=now.isoformat(),
            duration_s=round(float(duration_s), 1),
            transcript=transcript.strip(),
        )
        self._db.voice_notes.append(asdict(vn))
        self._save()
        return vn

    def voice_notes(self) -> list[VoiceNote]:
        return [VoiceNote(**v) for v in self._db.voice_notes]

    # -- reminders ---------------------------------------------------------
    def add_reminder(
        self, text: str, *, due: datetime, now: datetime, category: str = "reminder"
    ) -> Reminder:
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
        return [Reminder(**r) for r in self._db.reminders if not r.get("fired")]

    def due(self, now: datetime) -> list[Reminder]:
        """Return + mark fired every pending reminder whose time has come."""
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
