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
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from blazend.domains.ai_orchestrator.adapters.rpi5.tools import Tools

# Tool-backed command intents (Phase 4d): routed here to the shared Tools in the
# current pipeline; the Rust mind takes this over at the cutover. The tool names
# mirror jessica_core::dispatch + configs/intents/system.yaml.
_TOOL_INTENTS: frozenset[str] = frozenset({
    "weather.query", "news.brief", "web.lookup", "radio.play", "radio.stop",
    "music.play", "music.next", "music.prev", "audiobook.play",
    "library.search",
    "context.recall", "context.recall_reminders", "context.remember", "context.set_name",
    "context.add_reminder",
})

# Sensible defaults for delta-based mutations when nothing is stored yet.
_DEFAULTS: dict[str, Any] = {"audio.volume": 30}  # low default: less speaker→mic echo
_CONFIRMS_NEEDED = {"never": 0, "single": 1, "loud": 1, "double_loud": 2}

# Spoken language tokens (PL + EN trigger captures) → ISO code. Only pl/en are
# supported (Polish-first, English co-equal — docs/13-LANGUAGES.md); the extra
# tokens resolve so we can answer "I only speak Polish and English".
_LANG_TOKENS: dict[str, str] = {
    "polish": "pl", "polski": "pl", "polsku": "pl",
    "english": "en", "angielski": "en", "angielsku": "en",
    "german": "de", "niemiecki": "de", "niemiecku": "de",
    "french": "fr", "francuski": "fr", "francusku": "fr",
    "spanish": "es", "hiszpański": "es", "hiszpańsku": "es",
    "hiszpanski": "es", "hiszpansku": "es",
}


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

    def __init__(
        self, intents_cfg: dict[str, Any], policy_cfg: dict[str, Any], settings: SettingsStore
    ):
        self._intents = {i["name"]: i for i in intents_cfg.get("intents", [])}
        self._allow = policy_cfg.get("allow_voice_mutation", {})
        self._deny = policy_cfg.get("deny_voice_mutation", [])
        self._settings = settings
        self._pending: dict[str, Any] | None = None
        allowed = self._allow.get("languages.pinned", {}).get("allowed_values", ["pl", "en"])
        self._supported = tuple(c for c in allowed if c)
        self._tool_kit: Tools | None = None

    def _tools(self) -> Tools:
        """Lazily build the shared tool kit (clients read their configs)."""
        if self._tool_kit is None:
            from blazend.domains.ai_orchestrator.adapters.rpi5.tools import Tools

            self._tool_kit = Tools()
        return self._tool_kit

    def pinned_language(self) -> str | None:
        """Currently pinned reply language (None = auto-detect)."""
        pinned = self._settings.get("languages.pinned")
        return None if pinned is None else str(pinned)

    def _effective_lang(self, detected: str) -> str:
        """Pinned language wins over the per-utterance detected one."""
        return self.pinned_language() or detected

    def dispatch(self, name: str, params: dict[str, Any], language: str = "pl") -> DispatchResult:
        spec = self._intents.get(name)
        if spec is None:
            return DispatchResult(language=self._effective_lang(language), action="noop")
        # The reply language follows a pin if one is set; switch_language is the
        # one intent that drives its own (target) language instead.
        lang = self._effective_lang(language)
        action = spec.get("action")
        if action == "mutate":
            return self._mutate(spec["mutate"], params, lang)
        if action == "confirm_loud":
            return self._confirm(lang)
        if action == "cancel_pending":
            return self._cancel(lang)
        if action == "switch_language":
            return self._switch_language(params, lang)
        if action == "unpin_language":
            return self._unpin_language(lang)
        if action == "tool_call":
            return self._tool(spec.get("tool", ""), params, lang)
        if action in ("tts_interrupt", "orchestrator_sleep", "orchestrator_resume"):
            sig = {"tts_interrupt": "tts_interrupt", "orchestrator_sleep": "sleep",
                   "orchestrator_resume": "resume"}[action]
            return DispatchResult(language=lang, action="signal", signal=sig)
        if action == "say":
            # Canned, bilingual spoken response carried by the intent spec
            # (e.g. the capabilities/help intent). Polish-first.
            resp = spec.get("response", {})
            return DispatchResult(
                _t(lang, str(resp.get("pl", "")), str(resp.get("en", ""))),
                lang, "applied", data={"intent": name},
            )
        return DispatchResult(language=lang, action="noop", data={"intent": name})

    # -- mutate + confirmation ---------------------------------------------
    def _denied(self, key: str) -> bool:
        for pat in self._deny:
            if fnmatch.fnmatch(key, pat.replace("**", "*")):
                return True
        return key not in self._allow

    def _resolve_value(self, mut: dict[str, Any], params: dict[str, Any]) -> Any:
        if "value" in mut:
            return mut["value"]
        if "value_from_group" in mut:
            raw = params.get(mut["value_from_group"])
            if raw is None:
                return None
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

    def _mutate(self, mut: dict[str, Any], params: dict[str, Any], lang: str) -> DispatchResult:
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

    # -- language pin ------------------------------------------------------
    def _switch_language(self, params: dict[str, Any], lang: str) -> DispatchResult:
        token = str(params.get("lang", "")).strip().lower()
        code = _LANG_TOKENS.get(token)
        if code is None:
            return DispatchResult(
                _t(lang, "Nie zrozumiałam, na jaki język.", "I didn't catch which language."),
                lang, "denied")
        if code not in self._supported:
            return DispatchResult(
                _t(lang, "Mówię tylko po polsku i angielsku.", "I only speak Polish and English."),
                lang, "denied", data={"requested": code})
        self._settings.set("languages.pinned", code)
        # Confirm in the *target* language so the user hears the switch land.
        speak = _t(code, "Od teraz mówię po polsku.", "From now on I'll speak English.")
        return DispatchResult(speak, code, "applied", data={"key": "languages.pinned", "value": code})

    def _unpin_language(self, lang: str) -> DispatchResult:
        self._settings.set("languages.pinned", None)
        return DispatchResult(
            _t(lang, "Słucham automatycznie.", "I'll auto-detect the language now."),
            lang, "applied", data={"key": "languages.pinned", "value": None})

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
    def _tool(
        self, tool: str, params: dict[str, Any], lang: str, *, now: datetime | None = None
    ) -> DispatchResult:
        now = now or datetime.now()
        if tool in _TOOL_INTENTS:
            res = self._tools().run(tool, params, lang)
            return DispatchResult(res.text, lang, "tool", data={"tool": tool, "ok": res.ok, **res.payload})
        if tool == "clock.time":
            # Spell the time out in Polish words so Piper says it naturally
            # ("dwudziesta trzecia szesnaście") instead of mangling "23:16".
            from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.plnum import time_words

            pl = f"Jest godzina {time_words(now.hour, now.minute)}."
            return DispatchResult(_t(lang, pl, f"It's {now:%H:%M}."), lang, "tool")
        if tool == "clock.date":
            return DispatchResult(_t(lang, f"Dziś jest {now:%d.%m.%Y}.", f"Today is {now:%Y-%m-%d}."),
                                  lang, "tool")
        if tool == "languages.list":
            return DispatchResult(_t(lang, "Mówię po polsku i angielsku.", "I speak Polish and English."),
                                  lang, "tool", data={"languages": list(self._supported)})
        return DispatchResult(_t(lang, "Jeszcze tego nie potrafię.", "I can't do that yet."),
                              lang, "tool", data={"tool": tool, "unimplemented": True})
