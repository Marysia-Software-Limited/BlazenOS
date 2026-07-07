"""jessica_linux — the Jessica agent for Linux nodes (paul now, the Pi later).

Reuses the portable conversational engine (`blazend...assistant.engine.Assistant`)
and wires its adapters to a node's **mesh** resources: the LLM is the node's GPU
Ollama-11b resolved from `configs/mesh.yaml`, not a local llama.cpp Bielik. A Linux
node is a server, so the interface is a CLI/REPL — no wake word.
"""
from __future__ import annotations

from jessica_linux.node import build_assistant, build_router

__all__ = ["build_assistant", "build_router"]
