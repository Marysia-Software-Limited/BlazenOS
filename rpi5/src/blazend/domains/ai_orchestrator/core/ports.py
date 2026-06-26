"""Ports for the ai-orchestrator domain.

The orchestrator routes a turn across interchangeable AI backends. Every backend
speaks the same chat surface — the local-ai ``LlmPort`` — so the on-device Bielik,
a LAN Ollama box, OpenAI, and Gemini are swappable behind one type. The concrete
selection lives in :mod:`.registry`. See docs/19-DOMAIN-ARCHITECTURE.md.
"""

from __future__ import annotations

from blazend.domains.local_ai.core.ports import LlmPort

__all__ = ["LlmPort"]
