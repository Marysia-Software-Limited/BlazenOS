"""Remote LLM backend via **Ollama** — for development on a LAN GPU box.

The on-device Bielik runs on the Pi 5 CPU (~2 tok/s). For testing it is far
nicer to point the brain at an Ollama server on the LAN (e.g. Bielik 11B on a
3090 at ``http://192.168.50.102:11434``) and get GPU-speed, larger-model replies.

This exposes the same surface as :class:`blazend.domains.local_ai.adapters.rpi5.localllm.LocalLlm`
(``available`` / ``chat`` / ``chat_stream``) so the engine prefers it
transparently. It is **opt-in** via ``BLAZEN_LLM_OLLAMA_URL`` and reaches only
the configured dev LAN host over plain HTTP — never a cloud service; the shipped
appliance default stays fully on-device.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Iterator

from blazend.domains.local_ai.adapters.rpi5.localllm import LlmError

_DEFAULT_MODEL = "SpeakLeash/bielik-11b-v2.3-instruct:Q8_0"


class OllamaError(LlmError):
    """Raised when the Ollama endpoint cannot serve a completion.

    Subclasses :class:`LlmError` so the engine's ``except LlmError`` path handles
    a remote failure exactly like a local-model failure.
    """


class OllamaLlm:
    """Minimal Ollama chat client matching the ``LocalLlm`` surface."""

    def __init__(
        self,
        url: str | None = None,
        *,
        model: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.url = (url if url is not None else os.environ.get("BLAZEN_LLM_OLLAMA_URL", "")).rstrip(
            "/"
        )
        self.model = model or os.environ.get("BLAZEN_LLM_OLLAMA_MODEL", _DEFAULT_MODEL)
        self.timeout = timeout

    @property
    def available(self) -> bool:
        """True when the endpoint is configured and reachable."""
        if not self.url:
            return False
        try:
            with urllib.request.urlopen(f"{self.url}/api/version", timeout=3) as resp:
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
        payload: dict[str, object] = {
            "model": self.model, "messages": self._messages(user, system), "stream": stream}
        # Power saving (2026-08-23): how long Ollama keeps the model in VRAM
        # after a reply — the GPU can't reach its idle P-state while loaded.
        # Ollama's own default is 5m; BLAZEN_LLM_KEEP_ALIVE overrides ("2m",
        # "0" = unload immediately, "-1" = keep forever).
        if keep := os.environ.get("BLAZEN_LLM_KEEP_ALIVE"):
            payload["keep_alive"] = keep
        body = json.dumps(payload).encode("utf-8")
        return urllib.request.Request(
            f"{self.url}/api/chat",
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
            raise OllamaError(f"ollama chat failed: {exc}") from exc
        return str(data.get("message", {}).get("content", "")).strip()

    def chat_stream(self, user: str, *, system: str | None = None) -> Iterator[str]:
        """Token/sentence chunks as they arrive (newline-delimited JSON)."""
        try:
            with urllib.request.urlopen(
                self._request(stream=True, user=user, system=system), timeout=self.timeout
            ) as resp:
                for raw in resp:
                    line = raw.strip()
                    if not line:
                        continue
                    chunk = json.loads(line.decode("utf-8"))
                    piece = chunk.get("message", {}).get("content", "")
                    if piece:
                        yield str(piece)
                    if chunk.get("done"):
                        break
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            raise OllamaError(f"ollama stream failed: {exc}") from exc
