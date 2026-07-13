"""mesh_llm — portable client for a mesh LLM peer (one Jessica, many brains).

Any node can advertise an ``llm`` resource of ``kind: openai`` in
``configs/mesh.yaml`` (rachel serves Bielik-11B + Qwen2.5-72B over ``mlx_lm.server``;
paul serves Ollama). :class:`MeshLlm` speaks the OpenAI ``/v1/chat/completions``
wire so any consumer — rachel's chat, a mobile node, the Pi router — calls a peer
by URL without a per-model adapter. :func:`pick` resolves the first *reachable*
backend from an ordered preference (locality-aware routing, strict-improvement:
an offline peer just drops out). Pure stdlib + the mesh registry.
"""
from __future__ import annotations

from mesh_llm.client import MeshLlm, MeshLlmError, pick

__all__ = ["MeshLlm", "MeshLlmError", "pick"]
