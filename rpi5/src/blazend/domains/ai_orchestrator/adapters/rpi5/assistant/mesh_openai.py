"""Generic OpenAI-compatible LLM backend for a **mesh peer** on the LAN.

A peer node can advertise an ``llm`` resource of ``kind: openai`` in
``configs/mesh.yaml`` — e.g. rachel serves Bielik-11B and Qwen2.5-72B over
``mlx_lm.server`` (OpenAI-compatible ``POST /v1/chat/completions``). This client
lets the :class:`~blazend.domains.ai_orchestrator.core.model_router.ModelRouter`
route to any such peer by name without a hardcoded adapter per model.

Same surface as :class:`~blazend.domains.ai_orchestrator.adapters.rpi5.assistant.ollama.OllamaLlm`
(``available`` / ``chat`` / ``chat_stream``) so the router treats it identically.
It is **keyless** (a trusted LAN peer, not a cloud service) and **strict-improvement**:
``available`` is a cheap reachability probe, so an offline peer just drops out and
routing falls through to paul's Ollama / the on-device Bielik. It never reaches a
cloud endpoint — only the ``url`` the mesh resolved for that peer.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Iterator

from blazend.domains.local_ai.adapters.rpi5.localllm import LlmError


class MeshOpenAiError(LlmError):
    """Raised when a mesh OpenAI peer cannot serve a completion.

    Subclasses :class:`LlmError` so the engine's ``except LlmError`` path handles a
    peer failure exactly like a local-model failure (fall to the next backend).
    """


class MeshOpenAiLlm:
    """Minimal OpenAI chat-completions client for a LAN mesh peer.

    ``url`` is the peer's base (e.g. ``http://192.168.50.186:11436``); the chat
    endpoint is ``{url}/v1/chat/completions`` and the reachability probe is
    ``{url}/v1/models``.
    """

    def __init__(self, url: str, *, model: str = "", timeout: float = 120.0) -> None:
        self.url = url.rstrip("/")
        self.model = model
        self.timeout = timeout

    @property
    def available(self) -> bool:
        """True when the peer's OpenAI server is reachable (cheap ``/v1/models``)."""
        if not self.url:
            return False
        try:
            with urllib.request.urlopen(f"{self.url}/v1/models", timeout=3) as resp:
                return bool(getattr(resp, "status", 200) == 200)
        except (urllib.error.URLError, OSError):
            return False

    def _messages(self, user: str, system: str | None) -> list[dict[str, str]]:
        msgs: list[dict[str, str]] = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": user})
        return msgs

    def _request(self, *, stream: bool, user: str, system: str | None) -> urllib.request.Request:
        body = json.dumps(
            {"model": self.model, "messages": self._messages(user, system), "stream": stream}
        ).encode("utf-8")
        return urllib.request.Request(
            f"{self.url}/v1/chat/completions",
            data=body,
            headers={"Content-Type": "application/json"},
        )

    def chat(self, user: str, *, system: str | None = None) -> str:
        """One-shot reply (non-streaming)."""
        try:
            with urllib.request.urlopen(
                self._request(stream=False, user=user, system=system), timeout=self.timeout
            ) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            raise MeshOpenAiError(f"mesh openai chat failed: {exc}") from exc
        try:
            return str(data["choices"][0]["message"]["content"]).strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise MeshOpenAiError(f"unexpected response shape: {data!r}"[:200]) from exc

    def chat_stream(self, user: str, *, system: str | None = None) -> Iterator[str]:
        """Satisfy the ``Llm`` protocol: yield the whole reply as one chunk. The
        engine's sentence slicer still splits it for TTS."""
        yield self.chat(user, system=system)
