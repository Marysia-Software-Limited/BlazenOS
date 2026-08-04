"""Wire the portable Jessica engine to a Linux node's mesh resources.

paul (and later the Pi, as a Linux box) reuses the Pi's `Assistant` engine
verbatim; only the *adapters* differ. The LLM is the node's GPU **Ollama-11b**,
resolved from the shared mesh registry (`configs/mesh.yaml`) rather than a
hardcoded URL or a local llama.cpp Bielik. No wake word — a server node is
reached via CLI/REPL.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.engine import Assistant
from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.gemini import GeminiClient
from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.ollama import OllamaLlm
from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.openai import OpenAiClient
from blazend.domains.ai_orchestrator.core.model_router import ModelRouter
from mesh_registry import Mesh

_NO_LOCAL = "no local model on this node — reason over the mesh (Ollama/cloud)"

# Node-local processing (decision 2026-07-13): every node reasons on its OWN
# hardware. The shared llm.yaml expresses that for the Pi (all tasks →
# on-device bielik-1.5b), which a Linux GPU node cannot run (`_NoLocalModel`).
# This node's local brain is its GPU Ollama — the mesh resolves `ollama-11b`
# to this host — so override the task policy here instead of forking the
# appliance config.
_NODE_LOCAL_TASKS = {"routing": {"tasks": {
    "command": ["ollama-11b"],
    "recommend": ["ollama-11b"],
    "open_qa": ["ollama-11b"],
}}}


class _NoLocalModel:
    """Stand-in for the Pi's local llama.cpp Bielik. A Linux GPU node has none, so
    the router reports it unavailable and skips the on-device tiers, falling to
    Ollama-11b (and cloud for open_qa)."""

    def __init__(self, model: str = "") -> None:
        self.model = model

    @property
    def available(self) -> bool:
        return False

    def chat(self, user: str, *, system: str | None = None) -> str:
        raise RuntimeError(_NO_LOCAL)

    def chat_stream(self, user: str, *, system: str | None = None) -> Iterator[str]:
        raise RuntimeError(_NO_LOCAL)

    def close(self) -> None:  # pragma: no cover - nothing to release
        pass


def build_router(
    mesh: Mesh,
    *,
    openai: OpenAiClient | None = None,
    ollama: OllamaLlm | None = None,
) -> ModelRouter:
    """A `ModelRouter` that resolves its `ollama-11b` backend URL from the mesh
    (task→backend *policy* is this node's `_NODE_LOCAL_TASKS`; the mesh
    supplies the *where*). Tests may inject `ollama` to bypass resolution +
    the network."""
    return ModelRouter(
        cfg=_NODE_LOCAL_TASKS,
        ollama=ollama,  # None → the router resolves the URL from `mesh`
        openai=openai or OpenAiClient(),
        local_factory=lambda model: _NoLocalModel(model),
        mesh=mesh,
    )


def build_assistant(
    *,
    mesh: Mesh | None = None,
    data: Path | None = None,
    router: ModelRouter | None = None,
) -> Assistant:
    """The full Jessica agent for this node: portable engine + mesh-wired LLM,
    always awake (a server has no wake gate). Semantic memory recall uses the
    same wiring as the Pi's brain — memories synced over the fabric become
    prompt context here too (degrades to lexical recall when the e5 model or
    onnxruntime/tokenizers are absent)."""
    mesh = mesh or Mesh.load()
    router = router or build_router(mesh)
    from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.context_wiring import (
        notes_context_wiring,
    )
    from blazend.domains.context.adapters.rpi5.memory import MemoryStore

    wiring = notes_context_wiring()
    return Assistant(
        memory=MemoryStore(data),
        gemini=GeminiClient(),
        router=router,
        openai=OpenAiClient(),
        always_awake=True,
        embedder=wiring.embedder,
        notes_top_k=wiring.top_k,
        notes_min_score=wiring.min_score,
        notes_rel_margin=wiring.rel_margin,
        notes_max_chars=wiring.max_chars,
        notes_include_voice=wiring.include_voice,
    )
