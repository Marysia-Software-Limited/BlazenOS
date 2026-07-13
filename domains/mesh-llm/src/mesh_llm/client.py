"""OpenAI-compatible chat client for a LAN mesh LLM peer + a first-reachable resolver.

Keyless (a trusted LAN peer, not a cloud service): the chat endpoint is
``{url}/v1/chat/completions`` and the reachability probe is ``{url}/v1/models``.
Carries a full ``messages`` list so a caller keeps real conversation history
(system persona + prior turns), unlike a one-shot prompt.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Sequence
from typing import Any

from mesh_registry import Mesh, Resource

Message = dict[str, str]


class MeshLlmError(RuntimeError):
    """A mesh LLM peer could not serve a completion (unreachable or bad response)."""


class MeshLlm:
    """Minimal OpenAI chat-completions client for one mesh peer.

    ``url`` is the peer's base (e.g. ``http://192.168.50.186:11435``); ``model`` is
    the tag the peer serves (from the mesh resource's ``model`` attr).
    """

    def __init__(self, url: str, *, model: str = "", timeout: float = 120.0,
                 name: str = "") -> None:
        self.url = url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.name = name or url

    @classmethod
    def from_resource(cls, res: Resource, *, timeout: float = 120.0) -> MeshLlm:
        """Build a client from a mesh ``llm`` resource (``url`` + ``model`` attr)."""
        return cls(res.url or "", model=str(res.attrs.get("model", "")),
                   timeout=timeout, name=res.name)

    @property
    def available(self) -> bool:
        """True when the peer's OpenAI server answers a cheap ``/v1/models`` probe."""
        if not self.url:
            return False
        try:
            with urllib.request.urlopen(f"{self.url}/v1/models", timeout=3) as resp:  # noqa: S310 — LAN peer
                return bool(getattr(resp, "status", 200) == 200)
        except (urllib.error.URLError, OSError):
            return False

    def chat(self, messages: Sequence[Message], *, max_tokens: int = 512,
             temperature: float = 0.4) -> str:
        """Return the assistant reply for a full ``messages`` list (non-streaming)."""
        body = json.dumps({
            "model": self.model,
            "messages": list(messages),
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }).encode("utf-8")
        req = urllib.request.Request(  # noqa: S310 — LAN peer, http is expected
            f"{self.url}/v1/chat/completions", data=body,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
                data: Any = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            raise MeshLlmError(f"{self.name}: chat failed: {exc}") from exc
        try:
            return str(data["choices"][0]["message"]["content"]).strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise MeshLlmError(f"{self.name}: unexpected response {data!r}"[:200]) from exc


def pick(names: Sequence[str], *, mesh: Mesh | None = None,
         timeout: float = 120.0) -> MeshLlm | None:
    """First *reachable* ``llm`` backend from an ordered preference list.

    ``names`` are mesh ``llm`` resource names in priority order (locality-aware:
    this node's own model first, then peers). Returns a ready :class:`MeshLlm`, or
    ``None`` if none are advertised + reachable (caller decides the fallback).
    """
    mesh = mesh or Mesh.load()
    for name in names:
        try:
            res = mesh.resource("llm", name)
        except Exception:  # noqa: BLE001 — not advertised → try the next
            res = None
        if res is None or not res.url:
            continue
        client = MeshLlm.from_resource(res, timeout=timeout)
        if client.available:
            return client
    return None
