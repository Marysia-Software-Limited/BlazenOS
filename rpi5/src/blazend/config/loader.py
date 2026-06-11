"""Layered YAML loader for blazend configs."""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_SYSTEM_ROOTS: tuple[Path, ...] = (
    Path("/usr/share/blazen/defaults"),
    Path("/etc/blazen"),
)
DEFAULT_OVERRIDE_DIR = Path("/etc/blazen/overrides")


@dataclass(slots=True)
class Config:
    """Resolved configuration for one logical name (e.g. ``"asr"``).

    The raw merged tree is in :attr:`data`; convenience accessors
    (:meth:`get`) walk dotted paths.
    """

    name: str
    data: dict[str, Any]
    sources: list[Path] = field(default_factory=list)

    def get(self, path: str, default: Any = None) -> Any:
        """Walk a dotted path (``"audio.volume"``) and return the leaf."""
        node: Any = self.data
        for part in path.split("."):
            if not isinstance(node, Mapping) or part not in node:
                return default
            node = node[part]
        return node

    def require(self, path: str) -> Any:
        """Like :meth:`get`, but raise ``KeyError`` if the key is missing."""
        sentinel = object()
        value = self.get(path, sentinel)
        if value is sentinel:
            raise KeyError(f"{self.name}: missing required key {path!r}")
        return value


class ConfigLoader:
    """Resolves the layered YAML stack.

    ``BLAZEN_CONFIG_ROOT`` overrides the system roots — point it at the
    repo's ``configs/`` directory during dev so iteration needs no root.
    """

    def __init__(
        self,
        system_roots: Iterable[Path] | None = None,
        override_dir: Path | None = None,
        env_overrides: Mapping[str, str] | None = None,
    ) -> None:
        if (override_root := os.environ.get("BLAZEN_CONFIG_ROOT")) is not None:
            self._roots: tuple[Path, ...] = (Path(override_root),)
            self._override_dir = Path(override_root) / "overrides"
        else:
            self._roots = tuple(system_roots or DEFAULT_SYSTEM_ROOTS)
            self._override_dir = override_dir or DEFAULT_OVERRIDE_DIR
        self._env_overrides = dict(env_overrides or os.environ)

    def load(self, name: str) -> Config:
        """Load and merge the config named *name* (without the .yaml suffix)."""
        sources: list[Path] = []
        merged: dict[str, Any] = {}

        for root in self._roots:
            path = root / f"{name}.yaml"
            if path.is_file():
                _deep_merge_into(merged, _load_yaml(path))
                sources.append(path)

        admin = self._override_dir / "admin.yaml"
        if admin.is_file():
            _deep_merge_into(merged, _load_yaml(admin))
            sources.append(admin)

        voice = self._override_dir / "voice.yaml"
        if voice.is_file():
            _deep_merge_into(merged, _load_yaml(voice))
            sources.append(voice)

        _apply_env_overrides(merged, name, self._env_overrides)
        return Config(name=name, data=merged, sources=sources)


def load(name: str) -> Config:
    """Convenience wrapper around :class:`ConfigLoader`."""
    return ConfigLoader().load(name)


# ----- helpers ------------------------------------------------------------


def _load_yaml(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    parsed = yaml.safe_load(text) or {}
    if not isinstance(parsed, dict):
        raise ValueError(f"{path}: top-level YAML must be a mapping, got {type(parsed).__name__}")
    return parsed


def _deep_merge_into(dst: dict[str, Any], src: Mapping[str, Any]) -> None:
    for k, v in src.items():
        if isinstance(v, Mapping) and isinstance(dst.get(k), Mapping):
            _deep_merge_into(dst[k], v)
        else:
            dst[k] = v


# Map config names → env-var keys we honour. Conservative: only the small
# set documented in .env.example.
_ENV_MAP: dict[str, dict[str, str]] = {
    "asr": {"BLAZEN_ASR_MODEL": "active"},
    "llm": {"BLAZEN_LLM_MODEL": "active_model"},
    "tts": {"BLAZEN_TTS_VOICE_EN": "voice_by_language.en",
            "BLAZEN_TTS_VOICE_PL": "voice_by_language.pl"},
    "wake-word": {"BLAZEN_WAKE_WORDS": "active"},
}


def _apply_env_overrides(data: dict[str, Any], name: str, env: Mapping[str, str]) -> None:
    mapping = _ENV_MAP.get(name, {})
    for env_key, dotted in mapping.items():
        if (raw := env.get(env_key)) is None:
            continue
        value: Any = [s.strip() for s in raw.split(",")] if "," in raw else raw
        _set_dotted(data, dotted, value)


def _set_dotted(data: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    node = data
    for part in parts[:-1]:
        next_node = node.get(part)
        if not isinstance(next_node, dict):
            next_node = {}
            node[part] = next_node
        node = next_node
    node[parts[-1]] = value
