"""Ports for the context domain.

Two seams. ``MemoryStorePort`` is the persistence surface the conversation engine
depends on (notes, profile, reminders) — today's on-device JSON ``MemoryStore``
satisfies it; a future cross-device store (``blazend-fabric`` ``sync_fact``)
implements the same port so any device reasons over one shared memory — the "one
personality" goal. ``EmbedderPort`` is the existing semantic-recall seam.
See docs/19-DOMAIN-ARCHITECTURE.md and docs/16-SYNC-PROTOCOL.md.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from blazend.domains.context.adapters.rpi5.embeddings import EmbedderLike as EmbedderPort
from blazend.domains.context.adapters.rpi5.memory import Note, Reminder

__all__ = ["EmbedderPort", "MemoryStorePort"]


class MemoryStorePort(Protocol):
    """The store surface the engine needs. A subset of ``MemoryStore`` (structural
    typing lets the concrete store carry more — embeddings, voice notes)."""

    def add_note(self, text: str, *, now: datetime, title: str = "") -> Note: ...

    def notes(self) -> list[Note]: ...

    def recall(self, query: str | None = None) -> list[Note]: ...

    def set_profile(self, key: str, value: str, *, now: datetime) -> None: ...

    def get_profile(self, key: str, default: str | None = None) -> str | None: ...

    def add_reminder(
        self, text: str, *, due: datetime, now: datetime, category: str = "reminder"
    ) -> Reminder: ...

    def pending(self) -> list[Reminder]: ...

    def due(self, now: datetime) -> list[Reminder]: ...


if TYPE_CHECKING:
    # Static conformance: the rpi5 JSON store must satisfy the port.
    from blazend.domains.context.adapters.rpi5.memory import MemoryStore

    def _rpi5_store_conforms(store: MemoryStore) -> MemoryStorePort:
        return store
