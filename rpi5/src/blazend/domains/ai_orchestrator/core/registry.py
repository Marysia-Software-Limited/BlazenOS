"""Backend registry — pick the primary chat LLM the brain talks to.

Centralizes the selection that was inlined in :mod:`blazend.brain.__main__`:
prefer a reachable LAN Ollama box (dev GPU) when ``BLAZEN_LLM_OLLAMA_URL`` is set
and answering, otherwise the on-device Bielik (``LocalLlm``). Returns the single
*primary* backend; the cloud fallbacks (OpenAI / Gemini) sit behind the engine,
not here. Behaviour is identical to the old inline block — this just gives the
choice a named, testable home. See docs/19-DOMAIN-ARCHITECTURE.md.
"""

from __future__ import annotations

import logging
import os

from blazend.assistant.localllm import LocalLlm
from blazend.assistant.ollama import OllamaLlm
from blazend.domains.local_ai.core.ports import LlmPort

log = logging.getLogger("blazend.domains.ai_orchestrator.registry")


def select_chat_llm() -> LlmPort | None:
    """Resolve the primary chat backend, or ``None`` if none can be built.

    Order: reachable Ollama (``BLAZEN_LLM_OLLAMA_URL``) → on-device ``LocalLlm``.
    A construction failure degrades to ``None`` so the brain still serves and the
    engine's cloud fallback can take over.
    """
    try:
        ollama_url = os.environ.get("BLAZEN_LLM_OLLAMA_URL", "").strip()
        remote = OllamaLlm(ollama_url) if ollama_url else None
        if remote is not None and remote.available:
            log.info("brain LLM: remote Ollama %s (model=%s)", remote.url, remote.model)
            return remote
        if ollama_url:
            log.warning("Ollama %s unreachable — using on-device Bielik", ollama_url)
        return LocalLlm()
    except Exception:  # noqa: BLE001 — any backend build failure degrades to cloud
        log.warning("local LLM unavailable; freeform chat will fall back to cloud")
        return None
