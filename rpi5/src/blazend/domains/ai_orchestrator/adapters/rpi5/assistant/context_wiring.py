"""Shared semantic-recall wiring: ``embeddings.yaml`` → Embedder + knobs.

Both entrypoints that build an :class:`Assistant` — the live systemd brain
(`brain/__main__.py`) and the legacy single-process runner
(`voice/runner.py`) — need the same construction: read the config, build the
on-device e5 Embedder unless notes-context is disabled, and carry the
retrieval knobs. Keeping it here means the live pipeline can never silently
lose semantic recall again (it did until 2026-07-29: only the runner wired
the embedder, so the brain ran lexical-only).
"""
from __future__ import annotations

from dataclasses import dataclass

from blazend.config import load
from blazend.domains.context.adapters.rpi5.embeddings import Embedder


@dataclass(frozen=True)
class NotesWiring:
    """Everything `Assistant(...)` needs for semantic memory recall."""

    embedder: Embedder | None
    top_k: int = 4
    min_score: float = 0.82
    rel_margin: float = 0.06
    max_chars: int = 1200
    include_voice: bool = True  # voice-memo transcripts join the prompt context
    share_with_cloud: bool = False  # memories may reach cloud backends (privacy knob)


def notes_context_wiring() -> NotesWiring:
    """Build the semantic-recall wiring from ``embeddings.yaml``.

    Degrades cleanly: config unreadable or ``notes_context.enabled: false`` →
    ``embedder=None`` and the engine falls back to lexical recall (the CPU
    path is the contract; recall must never hard-fail)."""
    try:
        emb_cfg = load("embeddings")
    except Exception:  # noqa: BLE001 — any config problem → lexical recall
        return NotesWiring(embedder=None)
    nc = emb_cfg.get("notes_context", {}) or {}
    return NotesWiring(
        embedder=Embedder(config=emb_cfg) if nc.get("enabled", True) else None,
        top_k=int(nc.get("top_k", 4)),
        min_score=float(nc.get("min_score", 0.82)),
        rel_margin=float(nc.get("rel_margin", 0.06)),
        max_chars=int(nc.get("max_chars", 1200)),
        include_voice=bool(nc.get("include_voice_memos", True)),
        share_with_cloud=bool(nc.get("share_with_cloud", False)),
    )


__all__ = ["NotesWiring", "notes_context_wiring"]
