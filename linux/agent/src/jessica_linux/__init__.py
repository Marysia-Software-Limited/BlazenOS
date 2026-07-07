"""jessica_linux — the Jessica agent for Linux nodes (paul now, the Pi later).

Reuses the portable conversational engine (`blazend...assistant.engine.Assistant`)
and wires its adapters to a node's **mesh** resources: the LLM is the node's GPU
Ollama-11b resolved from `configs/mesh.yaml`, not a local llama.cpp Bielik. A Linux
node is a server, so the interface is a CLI/REPL — no wake word.
"""
from __future__ import annotations

from typing import Any

__all__ = ["build_assistant", "build_router"]


def __getattr__(name: str) -> Any:
    # Lazy: `node` imports the heavy `blazend` engine (rpi5), absent on some nodes
    # (macOS/rachel). Keep `import jessica_linux` cheap so the mesh/media/audiobook
    # tooling runs everywhere; only touching build_assistant/build_router pays for it.
    if name in __all__:
        from jessica_linux import node
        return getattr(node, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
