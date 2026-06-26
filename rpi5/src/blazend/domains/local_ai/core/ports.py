"""Ports for the local-ai domain.

The chat surface an embedded model exposes. The canonical port is the existing
``blazend.assistant.localllm.Llm`` Protocol (``available`` / ``chat`` /
``chat_stream``), which ``LocalLlm`` (llama.cpp) already satisfies — promoted here
as ``LlmPort`` so every adapter (llama-cpp today; Hailo / Apple / Android later)
binds to one stable name. See docs/19-DOMAIN-ARCHITECTURE.md.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from blazend.assistant.localllm import Llm as LlmPort
from blazend.assistant.localllm import LlmError

__all__ = ["LlmError", "LlmPort"]

if TYPE_CHECKING:
    # Static conformance: the rpi5 adapter must satisfy the port. mypy fails here
    # if LocalLlm ever drifts from LlmPort.
    from blazend.assistant.localllm import LocalLlm

    def _rpi5_adapter_conforms(adapter: LocalLlm) -> LlmPort:
        return adapter
