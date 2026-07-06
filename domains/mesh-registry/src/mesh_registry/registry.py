"""Load + query the constellation registry (``configs/mesh.yaml``).

A :class:`Resource` is one advertised capability (an LLM / ASR / TTS endpoint or
a local capability) on a node, carrying that node's name + host so a caller can
reach it. :class:`Mesh` is the whole registry with lookup helpers. Pure data —
no network; probing reachability is the caller's job.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_DEFAULT = "/etc/blazen/mesh.yaml"


def _repo_default() -> Path:
    # registry.py -> mesh_registry -> src -> mesh-registry -> domains -> repo root
    return Path(__file__).resolve().parents[4] / "configs" / "mesh.yaml"


@dataclass
class Resource:
    node: str                       # which node advertises it
    host: str                       # that node's LAN address
    kind: str                       # "xtts" / "openai" / "apple" / "piper" / ...
    name: str                       # resource id, e.g. "xtts", "ollama-11b"
    category: str                   # "llm" | "asr" | "tts"
    url: str | None = None          # network endpoint (None for local-only)
    local: bool = False
    attrs: dict[str, Any] = field(default_factory=dict)  # model/language/speaker/...

    @property
    def reachable_url(self) -> str | None:
        return self.url


class Mesh:
    def __init__(self, data: dict[str, Any], *, self_node: str | None = None) -> None:
        self._nodes: dict[str, Any] = data.get("nodes", {}) or {}
        self.self_node = os.environ.get("BLAZEN_NODE") or self_node or data.get("self")

    @classmethod
    def load(cls, path: str | None = None) -> Mesh:
        p = Path(path or os.environ.get("BLAZEN_MESH", "") or _DEFAULT)
        if not p.exists():
            p = _repo_default()
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            data = {}
        return cls(data)

    @property
    def nodes(self) -> list[str]:
        return list(self._nodes)

    def host(self, node: str) -> str | None:
        n = self._nodes.get(node)
        return n.get("host") if n else None

    def _iter(self, category: str):
        for node, ndata in self._nodes.items():
            host = ndata.get("host", "")
            for name, attrs in (ndata.get("resources", {}).get(category, {}) or {}).items():
                attrs = attrs or {}
                yield Resource(
                    node=node, host=host, name=name, category=category,
                    kind=attrs.get("kind", name), url=attrs.get("url"),
                    local=bool(attrs.get("local", False)), attrs=attrs,
                )

    def resources(self, category: str) -> list[Resource]:
        """Every resource of a category (``llm`` / ``asr`` / ``tts``) across nodes."""
        return list(self._iter(category))

    def resource(self, category: str, name: str) -> Resource | None:
        """A specific resource by category + name (e.g. ``tts``/``xtts``)."""
        return next((r for r in self._iter(category) if r.name == name), None)


__all__ = ["Mesh", "Resource"]
