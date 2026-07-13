"""Converse with Dżesika on the Mac — the LLM-consumer side of the rachel node.

rachel *serves* MLX models to the constellation; this is the other half — rachel
using them. Node-local by default (rachel's own MLX Bielik-11B for quick turns,
Qwen2.5-72B for deep/recommend), mirroring the constellation's node-local
processing decision (each node thinks on its own brain). Loads Jessica's Polish
persona from ``configs/llm.yaml`` and recalls recent shared-context notes from the
fabric so she remembers what you told the Pi. All shared logic is in domains
(:mod:`mesh_llm`, :mod:`mesh_registry`); this file only wires persona + policy.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from mesh_llm import MeshLlm, pick

# rachel's node-local brains, in preference order per task class. Deep tasks lead
# with the 72B; quick turns lead with the snappy Bielik-11B. Both end at the other
# tier so one server being down still answers (strict-improvement).
_TASK_BACKENDS: dict[str, list[str]] = {
    "command": ["mlx-bielik-11b", "mlx-qwen72b"],
    "recommend": ["mlx-qwen72b", "mlx-bielik-11b"],
    "open_qa": ["mlx-qwen72b", "mlx-bielik-11b"],
}

# Words that hint a turn wants the deep brain (recommendations / open reasoning).
_DEEP_HINTS = ("poleć", "polec", "dlaczego", "wyjaśnij", "wyjasnij", "porównaj",
               "porownaj", "opowiedz", "napisz", "zaplanuj", "recommend")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _llm_config() -> dict[str, Any]:
    cfg = os.environ.get("BLAZEN_LLM_CONFIG")
    path = Path(cfg) if cfg else _repo_root() / "configs" / "llm.yaml"
    try:
        import yaml  # noqa: PLC0415
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, ImportError):
        return {}
    return data if isinstance(data, dict) else {}


def persona() -> str:
    """Jessica's system prompt — the shared persona, so the Mac speaks in one voice."""
    return str(_llm_config().get("system_prompt", "")).strip() or (
        "Jesteś Dżesika, asystentka głosowa. Odpowiadaj po polsku, krótko.")


def classify(utterance: str) -> str:
    """Pick a task class for routing: deep brain for open/recommend turns, else quick."""
    low = utterance.lower()
    return "recommend" if any(h in low for h in _DEEP_HINTS) else "command"


def recall_context(*, memory_path: Path, limit: int = 5) -> str:
    """A short recap of recent shared-context notes (from the fabric) for the prompt,
    so Dżesika remembers what was said on any node. Empty string if none."""
    try:
        import json  # noqa: PLC0415
        mem = json.loads(memory_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    notes = mem.get("notes") if isinstance(mem, dict) else None
    if not isinstance(notes, list) or not notes:
        return ""
    recent = [str(n.get("text", n)).strip() for n in notes[-limit:] if n]
    recent = [t for t in recent if t]
    if not recent:
        return ""
    return "Notatki użytkownika (z innych urządzeń):\n" + "\n".join(f"- {t}" for t in recent)


class RachelChat:
    """A short-memory conversational session backed by rachel's node-local MLX."""

    def __init__(self, *, memory_path: Path | None = None, mesh: Any = None,
                 max_turns: int = 8) -> None:
        self._mesh = mesh
        self._memory_path = memory_path
        self._max_turns = max_turns
        self._history: list[dict[str, str]] = []

    def _backend(self, task: str) -> MeshLlm | None:
        names = _TASK_BACKENDS.get(task, _TASK_BACKENDS["command"])
        return pick(names, mesh=self._mesh)

    def _system(self) -> str:
        sys = persona()
        if self._memory_path:
            recap = recall_context(memory_path=self._memory_path)
            if recap:
                sys = f"{sys}\n\n{recap}"
        return sys

    def ask(self, utterance: str) -> str:
        """Answer one turn (with history), or a plain Polish note if no brain is up."""
        task = classify(utterance)
        llm = self._backend(task)
        if llm is None:
            return ("Przepraszam, w tej chwili nie mam dostępu do modelu — "
                    "sprawdź, czy serwer MLX działa.")
        messages = [{"role": "system", "content": self._system()}, *self._history,
                    {"role": "user", "content": utterance}]
        max_tokens = 512 if task != "command" else 256
        reply = llm.chat(messages, max_tokens=max_tokens)
        self._history += [{"role": "user", "content": utterance},
                          {"role": "assistant", "content": reply}]
        # Keep only the last N turns (2 messages per turn) to bound the prompt.
        self._history = self._history[-2 * self._max_turns:]
        return reply


__all__ = ["RachelChat", "classify", "persona", "recall_context"]
