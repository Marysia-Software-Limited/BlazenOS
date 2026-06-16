"""OpenAI adapter — the cloud second layer behind the on-device LLM.

The conversation path is **local-first**: :class:`blazend.assistant.localllm.LocalLlm`
answers when a model is loaded; this client is the fallback for when the local
model is unavailable (or, later, for escalated queries). Same public surface as
:class:`blazend.assistant.gemini.GeminiClient` (``available`` / ``chat``) so the
engine can chain them.

Activates the moment ``OPENAI_API_KEY`` is set in the environment (sourced from
``.env`` at launch). No SDK dependency — just ``urllib`` — and the HTTP
``transport`` is injectable so tests run fully offline and deterministically.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

DEFAULT_MODEL = "gpt-4o-mini"
_ENDPOINT = "https://api.openai.com/v1/chat/completions"

# A transport takes (url, headers, json_body) and returns the parsed response.
Transport = Callable[[str, dict[str, str], dict[str, Any]], dict[str, Any]]


class OpenAiError(RuntimeError):
    """Raised when a live OpenAI call fails."""


def _http_transport(url: str, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (fixed host)
            result: dict[str, Any] = json.loads(resp.read().decode("utf-8"))
            return result
    except urllib.error.HTTPError as e:  # pragma: no cover - network path
        raise OpenAiError(f"OpenAI HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}") from e
    except urllib.error.URLError as e:  # pragma: no cover - network path
        raise OpenAiError(f"OpenAI unreachable: {e.reason}") from e


class OpenAiClient:
    """Thin client over the OpenAI chat-completions endpoint."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        model: str | None = None,
        temperature: float = 0.4,
        transport: Transport | None = None,
    ):
        self.api_key = api_key if api_key is not None else os.environ.get("OPENAI_API_KEY", "")
        self.model = model or os.environ.get("OPENAI_MODEL") or DEFAULT_MODEL
        self.temperature = temperature
        self._transport = transport or _http_transport

    @property
    def available(self) -> bool:
        """True when an OpenAI call can actually be made (key present)."""
        return bool(self.api_key)

    def chat(self, user: str, *, system: str | None = None) -> str:
        """Freeform reply to ``user`` (OpenAI replies in the user's language)."""
        if not self.available:
            raise OpenAiError("OPENAI_API_KEY is not set")
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        resp = self._transport(_ENDPOINT, headers, body)
        return _extract_text(resp)


def _extract_text(resp: dict[str, Any]) -> str:
    try:
        text = str(resp["choices"][0]["message"]["content"]).strip()
    except (KeyError, IndexError, TypeError) as e:
        raise OpenAiError(f"unexpected OpenAI response shape: {resp!r}"[:300]) from e
    if not text:
        raise OpenAiError("OpenAI returned an empty response")
    return text
