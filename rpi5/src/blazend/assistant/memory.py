"""Persistent memory: terms/notes the user asks to remember, and reminders.

Stored as a flat JSON log of fabric-shaped facts (``note_created`` /
``reminder_created``) so it can later be promoted onto the Rust
`blazend-fabric` SyncLog and synced across devices. For the prototype it is a
single local JSON file; reminders carry an ISO ``due`` and a ``fired`` flag.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


def data_dir() -> Path:
    """Where the prototype persists memory. Override with ``BLAZEN_DATA_DIR``."""
    root = os.environ.get("BLAZEN_DATA_DIR")
    if root:
        return Path(root)
    runtime = os.environ.get("BLAZEN_RUNTIME_DIR", f"/tmp/blazen-{os.getuid()}")
    return Path(runtime) / "data"


@dataclass
class Note:
    """A remembered term/fact."""

    id: str
    text: str
    created: str  # ISO timestamp
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
    def add_note(self, text: str, *, now: datetime) -> Note:
        note = Note(id=self._next_id("note"), text=text.strip(), created=now.isoformat())
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
        return [n for n in notes if q in n.text.casefold()]

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
