"""OpenAI adapter — the cloud second layer behind the on-device LLM.

The conversation path is **local-first**: :class:`blazend.domains.local_ai.adapters.rpi5.localllm.LocalLlm`
answers when a model is loaded; this client is the fallback for when the local
model is unavailable (or, later, for escalated queries). Same public surface as
:class:`blazend.domains.ai_orchestrator.adapters.rpi5.assistant.gemini.GeminiClient` (``available`` / ``chat``) so the
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
from collections.abc import Callable, Iterator
from typing import Any

DEFAULT_MODEL = "gpt-4o-mini"
_ENDPOINT = "https://api.openai.com/v1/chat/completions"
# The Responses endpoint carries the hosted `web_search` tool — live internet
# research (news briefs) goes here; plain chat stays on chat-completions.
_RESPONSES_ENDPOINT = "https://api.openai.com/v1/responses"

# A transport takes (url, headers, json_body) and returns the parsed response.
Transport = Callable[[str, dict[str, str], dict[str, Any]], dict[str, Any]]


class OpenAiError(RuntimeError):
    """Raised when a live OpenAI call fails."""


def _http_transport(url: str, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers)
    # A web_search research call (reasoning model browsing live pages) routinely
    # needs 1-2 min; plain chat stays on the tight budget. Keyed off the URL so
    # the injectable Transport signature stays (url, headers, body).
    timeout = 150 if url == _RESPONSES_ENDPOINT else 30
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed host)
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
        temperature: float | None = 0.4,
        transport: Transport | None = None,
    ):
        # CHAT_GPT_KEY is the appliance's preferred name for the escalation key
        # (complex / news / knowledge questions the local Bielik punts upstream);
        # OPENAI_API_KEY stays accepted as the conventional fallback.
        self.api_key = (
            api_key
            if api_key is not None
            else (os.environ.get("CHAT_GPT_KEY") or os.environ.get("OPENAI_API_KEY", ""))
        )
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
        body: dict[str, Any] = {"model": self.model, "messages": messages}
        # Web-search models (e.g. gpt-4o-search-preview, used for live news) reject
        # `temperature`; omit it when None so those models are callable.
        if self.temperature is not None:
            body["temperature"] = self.temperature
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        try:
            resp = self._transport(_ENDPOINT, headers, body)
        except OpenAiError as e:
            # Reasoning-family models (gpt-5.6-sol etc.) accept only the default
            # temperature and 400 on any other value. Drop the param and retry
            # once so the model choice isn't coupled to sampling params.
            if "temperature" not in body or "'temperature'" not in str(e):
                raise
            del body["temperature"]
            resp = self._transport(_ENDPOINT, headers, body)
        return _extract_text(resp)

    def research(self, user: str, *, system: str | None = None) -> str:
        """Web-grounded answer: the Responses API with the hosted ``web_search``
        tool, so the model actually reads today's internet (used for the news
        and sport briefs). Raises :class:`OpenAiError` when the key is missing,
        the call fails, or no text came back — callers fall back to RSS."""
        if not self.available:
            raise OpenAiError("OPENAI_API_KEY is not set")
        body: dict[str, Any] = {
            "model": self.model,
            "tools": [{"type": "web_search"}],
            "input": user,
        }
        if system:
            body["instructions"] = system
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        resp = self._transport(_RESPONSES_ENDPOINT, headers, body)
        parts: list[str] = []
        for item in resp.get("output") or []:
            if item.get("type") != "message":
                continue
            for chunk in item.get("content") or []:
                if chunk.get("type") == "output_text" and chunk.get("text"):
                    parts.append(chunk["text"])
        text = "\n".join(parts).strip()
        if not text:
            raise OpenAiError("OpenAI research returned no text")
        return text

    def chat_stream(self, user: str, *, system: str | None = None) -> Iterator[str]:
        """Satisfy the ``Llm`` protocol: yield the whole reply as one chunk (no
        token streaming from the completions transport). The engine's sentence
        slicer still splits it for TTS."""
        yield self.chat(user, system=system)


def _extract_text(resp: dict[str, Any]) -> str:
    try:
        text = str(resp["choices"][0]["message"]["content"]).strip()
    except (KeyError, IndexError, TypeError) as e:
        raise OpenAiError(f"unexpected OpenAI response shape: {resp!r}"[:300]) from e
    if not text:
        raise OpenAiError("OpenAI returned an empty response")
    return text
