"""Static compiled-prompt loader — the runtime half of the DSPy pipeline.

DSPy signatures (``BookQuery → Recommendation``, ``OpenQuestion → Answer``) are
optimised OFFLINE on paul by ``scripts/compile-prompts.py`` and exported as static
JSON under ``configs/prompts/<name>.json`` (an optimised instruction + a handful
of few-shot demos). This module loads those artifacts and renders the final
prompt for whatever backend the :class:`ModelRouter` picked — so the Pi needs no
``dspy`` dependency at runtime, just prompt-filling.

Each artifact:
    {"name", "system", "instruction", "user_template", "demos": ["<rendered>", …]}
If an artifact is missing, a built-in default keeps the feature working (just
without the offline optimisation), so the code is useful before the first compile.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

_DEFAULT_DIR = "/etc/blazen/prompts"

# Built-in defaults (used until compile-prompts.py ships an optimised artifact).
# PL-first; the model must answer in Polish. The book/music recommenders return a
# 1-based candidate index + a one-sentence pitch so the reply is parseable.
_DEFAULTS: dict[str, dict[str, Any]] = {
    "book_recommendation": {
        "system": (
            "Jesteś Jessica, głosowa asystentka. Polecasz audiobooki WYŁĄCZNIE z "
            "podanej listy dostępnych pozycji. Odpowiadaj po polsku, ciepło i "
            "zwięźle."
        ),
        "instruction": (
            "Użytkownik prosi o książkę: „{query}”. Oto dostępne pozycje:\n{candidates}\n"
            "Wybierz JEDNĄ najlepiej pasującą pozycję z listy (podaj jej numer). "
            "Nie wymyślaj własnej treści i nie powtarzaj instrukcji. Odpowiedz "
            "krótko, dwoma liniami:\nNUMER: <numer>\nPOLECAM: <jedno-dwa zdania po polsku>"
        ),
        "user_template": "",
        "demos": [],
    },
    "music_recommendation": {
        "system": (
            "Jesteś Jessica, głosowa asystentka. Polecasz muzykę WYŁĄCZNIE z podanej "
            "listy dostępnych utworów. Odpowiadaj po polsku, zwięźle."
        ),
        "instruction": (
            "Użytkownik prosi o muzykę: „{query}”. Oto dostępne utwory:\n{candidates}\n"
            "Wybierz JEDEN najlepiej pasujący. Odpowiedz dokładnie w formacie:\n"
            "NUMER: <numer z listy>\nPOLECAM: <jedno zdanie>"
        ),
        "user_template": "",
        "demos": [],
    },
    "open_question": {
        "system": (
            "Jesteś Jessica, rzeczowa asystentka. Odpowiadasz po polsku, zwięźle i "
            "uczciwie; jeśli nie znasz odpowiedzi, powiedz to wprost."
        ),
        "instruction": "{query}",
        "user_template": "",
        "demos": [],
    },
}

_PICK = re.compile(r"NUMER\s*[:\-]?\s*(\d+)", re.IGNORECASE)
_PITCH = re.compile(r"POLECAM\s*[:\-]?\s*([^\n<]+)", re.IGNORECASE)


class PromptLibrary:
    """Loads compiled prompt artifacts (with built-in fallbacks) and renders them."""

    def __init__(self, *, prompts_dir: str | None = None) -> None:
        base = Path(prompts_dir or os.environ.get("BLAZEN_PROMPTS_DIR", _DEFAULT_DIR))
        self._specs: dict[str, dict[str, Any]] = dict(_DEFAULTS)
        try:
            for f in sorted(base.glob("*.json")):
                spec = json.loads(f.read_text(encoding="utf-8"))
                name = str(spec.get("name") or f.stem)
                self._specs[name] = {**_DEFAULTS.get(name, {}), **spec}
        except OSError:
            pass

    def render(self, name: str, **vars: Any) -> tuple[str, str]:
        """Return ``(system, user)`` for ``name`` with ``vars`` filled in. The
        few-shot demos (already rendered by the offline compiler) precede the live
        instruction."""
        spec = self._specs.get(name, _DEFAULTS.get(name, {}))
        system = str(spec.get("system", ""))
        instruction = str(spec.get("instruction", "{query}"))
        try:
            body = instruction.format(**vars)
        except (KeyError, IndexError):
            body = instruction
        demos = spec.get("demos", []) or []
        user = ("\n\n".join(str(d) for d in demos) + "\n\n" + body) if demos else body
        return system, user


def format_candidates(items: list[dict[str, Any]]) -> str:
    """Number the candidates for the prompt (1-based)."""
    lines = []
    for i, it in enumerate(items, 1):
        who = it.get("who", "")
        meta = ", ".join(x for x in (it.get("genre", ""), it.get("epoch", "")) if x)
        tail = f" ({meta})" if meta else ""
        lines.append(f"{i}. {it.get('title', '')}" + (f" — {who}" if who else "") + tail)
    return "\n".join(lines)


def parse_choice(reply: str, n: int) -> tuple[int, str]:
    """Parse ``NUMER: k`` / ``POLECAM: …`` from a model reply. Returns a 0-based
    index (clamped into range, default 0) and the pitch text (default: the whole
    reply)."""
    # Take the LAST NUMER/POLECAM — a small model may echo the instruction's
    # placeholder line before writing its real answer.
    idx = 0
    picks = _PICK.findall(reply)
    if picks:
        idx = max(0, min(n - 1, int(picks[-1]) - 1))
    pitches = [p.strip() for p in _PITCH.findall(reply) if p.strip() and "<" not in p]
    pitch = pitches[-1] if pitches else reply.strip()
    return idx, pitch


__all__ = ["PromptLibrary", "format_candidates", "parse_choice"]
