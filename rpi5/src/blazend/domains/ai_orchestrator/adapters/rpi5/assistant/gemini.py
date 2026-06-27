"""Gemini adapter — the assistant's window to up-to-date info (news, sites).

Real calls go to the Generative Language API and activate the moment
``GEMINI_API_KEY`` is set in the environment (or `.env`). No SDK dependency —
just ``urllib`` — and the HTTP ``transport`` is injectable so tests run fully
offline and deterministically.

Two modes:
  * :meth:`chat` — freeform conversation (used for Polish dialogue when no
    local LLM is loaded).
  * :meth:`grounded` — query with Google Search grounding, for "what's in the
    news" / "open this site and summarise".
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

DEFAULT_MODEL = "gemini-2.0-flash"
_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"

# A transport takes (url, json_body) and returns the parsed JSON response.
Transport = Callable[[str, dict[str, Any]], dict[str, Any]]


class GeminiError(RuntimeError):
    """Raised when a live Gemini call fails."""


def _http_transport(url: str, body: dict[str, Any]) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (fixed host)
            result: dict[str, Any] = json.loads(resp.read().decode("utf-8"))
            return result
    except urllib.error.HTTPError as e:  # pragma: no cover - network path
        raise GeminiError(f"Gemini HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}") from e
    except urllib.error.URLError as e:  # pragma: no cover - network path
        raise GeminiError(f"Gemini unreachable: {e.reason}") from e


class GeminiClient:
    """Thin client over the Gemini generateContent endpoint."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        model: str | None = None,
        transport: Transport | None = None,
    ):
        self.api_key = api_key if api_key is not None else os.environ.get("GEMINI_API_KEY", "")
        self.model = model or os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL
        self._transport = transport or _http_transport

    @property
    def available(self) -> bool:
        """True when a Gemini call can actually be made (key present)."""
        return bool(self.api_key)

    def _generate(
        self,
        contents: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        system: str | None = None,
    ) -> str:
        if not self.available:
            raise GeminiError("GEMINI_API_KEY is not set")
        body: dict[str, Any] = {"contents": contents}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if tools:
            body["tools"] = tools
        url = _ENDPOINT.format(model=self.model, key=self.api_key)
        resp = self._transport(url, body)
        return _extract_text(resp)

    def chat(self, user: str, *, system: str | None = None) -> str:
        """Freeform reply to ``user`` (Gemini replies in the user's language)."""
        return self._generate([{"role": "user", "parts": [{"text": user}]}], system=system)

    def grounded(self, query: str, *, system: str | None = None) -> str:
        """Answer ``query`` with Google Search grounding (fresh info)."""
        return self._generate(
            [{"role": "user", "parts": [{"text": query}]}],
            tools=[{"google_search": {}}],
            system=system,
        )


def _extract_text(resp: dict[str, Any]) -> str:
    try:
        parts = resp["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts).strip()
    except (KeyError, IndexError, TypeError) as e:
        raise GeminiError(f"unexpected Gemini response shape: {resp!r}"[:300]) from e
    if not text:
        raise GeminiError("Gemini returned an empty response")
    return text
