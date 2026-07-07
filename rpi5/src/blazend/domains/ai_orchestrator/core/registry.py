"""Backend registry — pick the primary chat LLM the brain talks to.

Centralizes the selection that was inlined in :mod:`blazend.domains.ai_orchestrator.adapters.rpi5.brain.__main__`:
prefer a reachable LAN Ollama box (dev GPU) when ``BLAZEN_LLM_OLLAMA_URL`` is set
and answering, otherwise the on-device Bielik (``LocalLlm``). Returns the single
*primary* backend; the cloud fallbacks (OpenAI / Gemini) sit behind the engine,
not here. Behaviour is identical to the old inline block — this just gives the
choice a named, testable home. See docs/19-DOMAIN-ARCHITECTURE.md.
"""

from __future__ import annotations

import logging
import os

from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.ollama import OllamaLlm
from blazend.domains.ai_orchestrator.core.model_router import ModelRouter
from blazend.domains.local_ai.adapters.rpi5.localllm import LocalLlm
from blazend.domains.local_ai.core.ports import LlmPort

log = logging.getLogger("blazend.domains.ai_orchestrator.registry")


def _ollama_url_from_mesh() -> str:
    """paul's Ollama endpoint from the mesh registry (the "where"), or "" so the
    caller falls back to the env-configured URL. Uses only a DEPLOYED registry
    ($BLAZEN_MESH or /etc/blazen/mesh.yaml), never the dev repo copy — keeps tests
    hermetic."""
    if not (os.environ.get("BLAZEN_MESH") or os.path.exists("/etc/blazen/mesh.yaml")):
        return ""
    try:
        from mesh_registry import Mesh  # noqa: PLC0415

        res = Mesh.load().resource("llm", "ollama-11b")
        return (res.url or "") if res else ""
    except Exception:  # noqa: BLE001 — no mesh → env fallback
        return ""


def build_model_router() -> ModelRouter | None:
    """Build the brain's task-based :class:`ModelRouter` (COMMAND→1.5B,
    RECOMMEND→4.5B, OPEN_QA→gpt-5.5, all→11B-Ollama when reachable). Backends are
    built lazily and individual build/availability failures degrade to the next
    entry, so this returns a router unless construction itself fails."""
    try:
        return ModelRouter()
    except Exception:  # noqa: BLE001 — degrade to the engine's Gemini/canned fallback
        log.warning("ModelRouter unavailable; freeform chat falls back to Gemini/canned")
        return None


def select_chat_llm() -> LlmPort | None:
    """Resolve the primary chat backend, or ``None`` if none can be built.

    Order: reachable Ollama (``BLAZEN_LLM_OLLAMA_URL``) → on-device ``LocalLlm``.
    A construction failure degrades to ``None`` so the brain still serves and the
    engine's cloud fallback can take over.
    """
    try:
        ollama_url = (_ollama_url_from_mesh() or os.environ.get("BLAZEN_LLM_OLLAMA_URL", "")).strip()
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
