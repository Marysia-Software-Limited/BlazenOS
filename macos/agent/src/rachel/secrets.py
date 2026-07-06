"""Parse ``macos/.secrets.env`` (gitignored). Never logs values.

Simple ``KEY=VALUE`` lines, ``#`` comments ignored. Used for the Azure premium
opt-in (``AZURE_SPEECH_KEY`` / ``AZURE_REGION``). The file is never committed and
values are never printed.
"""
from __future__ import annotations

from pathlib import Path


def _default_path() -> Path:
    # src/rachel/secrets.py → parents: [rachel, src, agent, macos]; secrets live at macos/.secrets.env
    return Path(__file__).resolve().parents[3] / ".secrets.env"


def load(path: str | None = None) -> dict[str, str]:
    p = Path(path) if path else _default_path()
    out: dict[str, str] = {}
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


__all__ = ["load"]
