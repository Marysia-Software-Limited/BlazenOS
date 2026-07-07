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
    """A `ModelRouter` whose `ollama-11b` backend is resolved from the mesh (this
    node's advertised endpoint). Task→backend *policy* still comes from
    `llm.yaml`; the mesh only supplies the *where*."""
    if ollama is None:
        res = mesh.resource("llm", "ollama-11b")
        ollama = OllamaLlm(url=res.url) if res and res.url else None
    return ModelRouter(
        ollama=ollama,
        openai=openai or OpenAiClient(),
        local_factory=lambda model: _NoLocalModel(model),
    )


def build_assistant(
    *,
    mesh: Mesh | None = None,
    data: Path | None = None,
    router: ModelRouter | None = None,
) -> Assistant:
    """The full Jessica agent for this node: portable engine + mesh-wired LLM,
    always awake (a server has no wake gate)."""
    mesh = mesh or Mesh.load()
    router = router or build_router(mesh)
    from blazend.domains.context.adapters.rpi5.memory import MemoryStore

    return Assistant(
        memory=MemoryStore(data),
        gemini=GeminiClient(),
        router=router,
        openai=OpenAiClient(),
        always_awake=True,
    )
