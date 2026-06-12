"""Intent dispatch — acting on `nlu.intent` (M5 system commands).

The fast-path router (`blazend-nlu`, Rust) only puts the intent *name* +
params on the wire; the **action** (mutate / tool / confirm level) is looked
up here from `configs/intents/system.yaml` + `configs/voice-policy.yaml`.
This module applies voice-mutable settings, enforces the confirmation grammar
(never / single / loud / double_loud), runs simple tools (clock), and emits
signals for lifecycle actions (stop talking, sleep, reboot…).

Pure + synchronous so the confirmation state machine is easy to test; the
orchestrator wires it onto the `nlu.intent` stream.
"""

from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

# Sensible defaults for delta-based mutations when nothing is stored yet.
_DEFAULTS: dict[str, Any] = {"audio.volume": 50}
_CONFIRMS_NEEDED = {"never": 0, "single": 1, "loud": 1, "double_loud": 2}


def _t(lang: str, pl: str, en: str) -> str:
    return pl if lang == "pl" else en


class SettingsStore:
    """Voice-mutated settings, persisted to JSON (overlay over config defaults)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._d: dict[str, Any] = json.loads(self.path.read_text()) if self.path.exists() else {}

    def get(self, key: str, default: Any = None) -> Any:
        return self._d.get(key, _DEFAULTS.get(key, default))

    def set(self, key: str, value: Any) -> None:
        self._d[key] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._d, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)


@dataclass
class DispatchResult:
    """Outcome of acting on one intent."""

    speak: str = ""
    language: str = "pl"
    action: str = "noop"  # applied | pending | confirmed | cancelled | denied | tool | signal | noop
    signal: str | None = None  # tts_interrupt | sleep | resume | reboot | shutdown
    data: dict[str, Any] = field(default_factory=dict)


class IntentDispatcher:
    """Acts on fast-path intents, holding the pending-confirmation state."""

    def __init__(self, intents_cfg: dict, policy_cfg: dict, settings: SettingsStore):
        self._intents = {i["name"]: i for i in intents_cfg.get("intents", [])}
        self._allow = policy_cfg.get("allow_voice_mutation", {})
        self._deny = policy_cfg.get("deny_voice_mutation", [])
        self._settings = settings
        self._pending: dict[str, Any] | None = None

    def dispatch(self, name: str, params: dict[str, Any], language: str = "pl") -> DispatchResult:
        spec = self._intents.get(name)
        if spec is None:
            return DispatchResult(language=language, action="noop")
        action = spec.get("action")
        if action == "mutate":
            return self._mutate(spec["mutate"], params, language)
        if action == "confirm_loud":
            return self._confirm(language)
        if action == "cancel_pending":
            return self._cancel(language)
        if action == "tool_call":
            return self._tool(spec.get("tool", ""), params, language)
        if action in ("tts_interrupt", "orchestrator_sleep", "orchestrator_resume"):
            sig = {"tts_interrupt": "tts_interrupt", "orchestrator_sleep": "sleep",
                   "orchestrator_resume": "resume"}[action]
            return DispatchResult(language=language, action="signal", signal=sig)
        return DispatchResult(language=language, action="noop", data={"intent": name})

    # -- mutate + confirmation ---------------------------------------------
    def _denied(self, key: str) -> bool:
        for pat in self._deny:
            if fnmatch.fnmatch(key, pat.replace("**", "*")):
                return True
        return key not in self._allow

    def _resolve_value(self, mut: dict, params: dict) -> Any:
        if "value" in mut:
            return mut["value"]
        if "value_from_group" in mut:
            raw = params.get(mut["value_from_group"])
            try:
                return int(raw)
            except (TypeError, ValueError):
                return raw
        if "delta" in mut:
            cur = self._settings.get(mut["key"], 0)
            val = cur + int(mut["delta"])
            if mut["key"] == "audio.volume":
                val = max(0, min(100, val))
            return val
        return None

    def _mutate(self, mut: dict, params: dict, lang: str) -> DispatchResult:
        key = mut["key"]
        if self._denied(key):
            return DispatchResult(_t(lang, "Tego nie mogę zmienić głosem.",
                                     "I can't change that by voice."), lang, "denied", data={"key": key})
        value = self._resolve_value(mut, params)
        if value is None:
            return DispatchResult(_t(lang, "Nie zrozumiałam wartości.", "I didn't catch the value."),
                                  lang, "denied")
        pol = self._allow.get(key, {})
        allowed = pol.get("allowed_values")
        if allowed is not None and value not in allowed:
            return DispatchResult(
                _t(lang, f"Nie mogę ustawić {key} na {value}.", f"I can't set {key} to {value}."),
                lang, "denied", data={"key": key, "value": value})
        need = _CONFIRMS_NEEDED.get(pol.get("confirm", "never"), 0)
        if need == 0:
            return self._apply(key, value, lang)
        self._pending = {"key": key, "value": value, "remaining": need, "language": lang}
        loud = pol.get("confirm") in ("loud", "double_loud")
        prompt = _t(lang,
                    "Na pewno? Powiedz „potwierdzam”." if loud else "Potwierdź: powiedz „potwierdzam”.",
                    "Are you sure? Say \"confirm\".")
        return DispatchResult(prompt, lang, "pending", data={"key": key, "value": value, "remaining": need})

    def _confirm(self, lang: str) -> DispatchResult:
        if not self._pending:
            return DispatchResult(_t(lang, "Nie ma nic do potwierdzenia.", "Nothing to confirm."), lang, "noop")
        self._pending["remaining"] -= 1
        if self._pending["remaining"] > 0:
            return DispatchResult(_t(lang, "Potwierdź jeszcze raz.", "Confirm once more."), lang, "pending")
        key, value, plang = self._pending["key"], self._pending["value"], self._pending["language"]
        self._pending = None
        return self._apply(key, value, plang, confirmed=True)

    def _cancel(self, lang: str) -> DispatchResult:
        if not self._pending:
            return DispatchResult(_t(lang, "Nie ma nic do anulowania.", "Nothing to cancel."), lang, "noop")
        self._pending = None
        return DispatchResult(_t(lang, "Anulowane.", "Cancelled."), lang, "cancelled")

    def _apply(self, key: str, value: Any, lang: str, *, confirmed: bool = False) -> DispatchResult:
        self._settings.set(key, value)
        signal = None
        if key == "system.power.reboot" and value:
            signal = "reboot"
        elif key == "system.power.shutdown" and value:
            signal = "shutdown"
        speak = self._confirmation_phrase(key, value, lang)
        return DispatchResult(speak, lang, "confirmed" if confirmed else "applied",
                              signal=signal, data={"key": key, "value": value})

    @staticmethod
    def _confirmation_phrase(key: str, value: Any, lang: str) -> str:
        if key == "audio.volume":
            return _t(lang, f"Głośność: {value}%.", f"Volume: {value}%.")
        if key == "system.power.reboot":
            return _t(lang, "Uruchamiam ponownie.", "Rebooting.")
        if key == "system.power.shutdown":
            return _t(lang, "Wyłączam się.", "Shutting down.")
        if key == "ssh.enabled":
            return _t(lang, f"SSH: {'włączone' if value else 'wyłączone'}.",
                      f"SSH: {'enabled' if value else 'disabled'}.")
        return _t(lang, f"Ustawione: {key} = {value}.", f"Set {key} = {value}.")

    # -- tools -------------------------------------------------------------
    def _tool(self, tool: str, params: dict, lang: str, *, now: datetime | None = None) -> DispatchResult:
        now = now or datetime.now()
        if tool == "clock.time":
            return DispatchResult(_t(lang, f"Jest {now:%H:%M}.", f"It's {now:%H:%M}."), lang, "tool")
        if tool == "clock.date":
            return DispatchResult(_t(lang, f"Dziś jest {now:%d.%m.%Y}.", f"Today is {now:%Y-%m-%d}."),
                                  lang, "tool")
        return DispatchResult(_t(lang, "Jeszcze tego nie potrafię.", "I can't do that yet."),
                              lang, "tool", data={"tool": tool, "unimplemented": True})
