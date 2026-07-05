"""Tier 0 — intent dispatch + the voice-policy confirmation grammar (M5)."""
from __future__ import annotations

from blazend.domains.ai_orchestrator.adapters.rpi5.dispatch import IntentDispatcher, SettingsStore

INTENTS = {"intents": [
    {"name": "volume_up", "action": "mutate", "mutate": {"key": "audio.volume", "delta": 10}},
    {"name": "volume_set", "action": "mutate", "mutate": {"key": "audio.volume", "value_from_group": "value"}},
    {"name": "reboot", "action": "mutate", "mutate": {"key": "system.power.reboot", "value": True}},
    {"name": "factory_reset", "action": "mutate", "mutate": {"key": "system.factory_reset", "value": True}},
    {"name": "enable_firewall", "action": "mutate", "mutate": {"key": "system.firewall.enabled", "value": True}},
    {"name": "set_engine_bad", "action": "mutate", "mutate": {"key": "llm.active_engine", "value": "gpu"}},
    {"name": "apply_change", "action": "confirm_loud"},
    {"name": "cancel_change", "action": "cancel_pending"},
    {"name": "stop_talking", "action": "tts_interrupt"},
    {"name": "what_time", "action": "tool_call", "tool": "clock.time"},
    {"name": "switch_language", "action": "switch_language"},
    {"name": "detect_language", "action": "unpin_language"},
    {"name": "list_languages", "action": "tool_call", "tool": "languages.list"},
]}
POLICY = {
    "allow_voice_mutation": {
        "audio.volume": {"confirm": "never"},
        "system.power.reboot": {"confirm": "loud"},
        "system.factory_reset": {"confirm": "double_loud"},
        "llm.active_engine": {"confirm": "single", "allowed_values": ["auto", "cpu", "hailo"]},
        "languages.pinned": {"confirm": "never", "allowed_values": ["pl", "en", None]},
    },
    "deny_voice_mutation": ["system.firewall.**"],
}


def _disp(tmp_path):
    return IntentDispatcher(INTENTS, POLICY, SettingsStore(tmp_path / "settings.json"))


def test_say_action_speaks_bilingual_response(tmp_path):
    """The `say` action (capabilities / help) returns the spec's PL/EN text."""
    intents = {"intents": [{
        "name": "what_can_you_do", "action": "say",
        "response": {"pl": "Jestem Jessica.", "en": "I'm Jessica."},
    }]}
    d = IntentDispatcher(intents, POLICY, SettingsStore(tmp_path / "s.json"))
    pl = d.dispatch("what_can_you_do", {}, "pl")
    en = d.dispatch("what_can_you_do", {}, "en")
    assert pl.action == "applied" and pl.speak == "Jestem Jessica."
    assert en.speak == "I'm Jessica."


def test_volume_mutate_no_confirm(tmp_path):
    d = _disp(tmp_path)
    r = d.dispatch("volume_up", {}, "pl")  # default 30 (echo-safe) + 10
    assert r.action == "applied" and r.data["value"] == 40 and "40%" in r.speak
    r2 = d.dispatch("volume_set", {"value": "25"}, "pl")
    assert r2.action == "applied" and r2.data["value"] == 25


def test_reboot_needs_loud_confirm(tmp_path):
    d = _disp(tmp_path)
    r = d.dispatch("reboot", {}, "pl")
    assert r.action == "pending" and "potwierdzam" in r.speak.lower()
    done = d.dispatch("apply_change", {}, "pl")
    assert done.action == "confirmed" and done.signal == "reboot"


def test_double_loud_needs_two_confirms(tmp_path):
    d = _disp(tmp_path)
    assert d.dispatch("factory_reset", {}, "pl").action == "pending"
    assert d.dispatch("apply_change", {}, "pl").action == "pending"   # one more
    assert d.dispatch("apply_change", {}, "pl").action == "confirmed"


def test_cancel_pending(tmp_path):
    d = _disp(tmp_path)
    d.dispatch("reboot", {}, "pl")
    assert d.dispatch("cancel_change", {}, "pl").action == "cancelled"
    assert d.dispatch("apply_change", {}, "pl").action == "noop"  # nothing pending


def test_denied_and_allowed_values(tmp_path):
    d = _disp(tmp_path)
    assert d.dispatch("enable_firewall", {}, "pl").action == "denied"   # deny glob
    assert d.dispatch("set_engine_bad", {}, "pl").action == "denied"    # not in allowed_values


def test_signals_and_tools(tmp_path):
    d = _disp(tmp_path)
    assert d.dispatch("stop_talking", {}, "pl").signal == "tts_interrupt"
    t = d.dispatch("what_time", {}, "pl")
    assert t.action == "tool" and t.speak.startswith("Jest")


def test_settings_persist(tmp_path):
    _disp(tmp_path).dispatch("volume_up", {}, "pl")  # 30 → 40 (persisted)
    assert _disp(tmp_path).dispatch("volume_up", {}, "pl").data["value"] == 50  # 40 → 50


def test_language_switch_pins_and_follows(tmp_path):
    """Scenario 09 in miniature: pin PL via an EN command, then replies follow
    the pin even for EN utterances, until unpinned back to auto-detect."""
    d = _disp(tmp_path)
    assert d.pinned_language() is None

    # "speak polish" — detected EN, but the confirmation lands in Polish.
    r = d.dispatch("switch_language", {"lang": "polish"}, "en")
    assert r.action == "applied" and r.language == "pl"
    assert "od teraz mówię po polsku" in r.speak.lower()
    assert d.pinned_language() == "pl"

    # A PL utterance under the PL pin → PL reply.
    assert d.dispatch("what_time", {}, "pl").language == "pl"
    # An EN utterance under the PL pin → still a PL reply (pin wins).
    en_under_pin = d.dispatch("what_time", {}, "en")
    assert en_under_pin.language == "pl" and en_under_pin.speak.startswith("Jest")

    # Unpin → auto-detect resumes, so an EN utterance now replies in EN.
    u = d.dispatch("detect_language", {}, "pl")
    assert u.action == "applied" and d.pinned_language() is None
    assert d.dispatch("what_time", {}, "en").language == "en"


def test_language_switch_rejects_unsupported(tmp_path):
    d = _disp(tmp_path)
    r = d.dispatch("switch_language", {"lang": "german"}, "en")
    assert r.action == "denied" and "polish and english" in r.speak.lower()
    assert d.pinned_language() is None  # pin unchanged
    miss = d.dispatch("switch_language", {"lang": "klingon"}, "en")
    assert miss.action == "denied" and d.pinned_language() is None


def test_languages_list_tool(tmp_path):
    d = _disp(tmp_path)
    en = d.dispatch("list_languages", {}, "en")
    assert en.action == "tool" and "polish and english" in en.speak.lower()
    assert en.data["languages"] == ["pl", "en"]
    pl = d.dispatch("list_languages", {}, "pl")
    assert "polsku i angielsku" in pl.speak.lower()


def test_orchestrator_mirrors_language_pin(tmp_path):
    from blazend.domains.systems.adapters.rpi5.orchestrator.supervisor import Orchestrator
    from blazend.events import Envelope

    disp = _disp(tmp_path)
    orch = Orchestrator(runtime_dir_=tmp_path, dispatcher=disp)
    reply = orch._dispatch_intent(
        Envelope(topic="nlu.intent", source="blazend-nlu",
                 data={"intent": "switch_language", "language": "en", "params": {"lang": "polish"}})
    )
    assert reply is not None and reply.data["language"] == "pl"
    assert disp.pinned_language() == "pl"


def test_orchestrator_dispatches_nlu_intent(tmp_path):
    from blazend.domains.systems.adapters.rpi5.orchestrator.supervisor import Orchestrator
    from blazend.events import Envelope

    orch = Orchestrator(runtime_dir_=tmp_path, dispatcher=_disp(tmp_path))
    reply = orch._dispatch_intent(
        Envelope(topic="nlu.intent", source="blazend-nlu",
                 data={"intent": "volume_up", "language": "pl", "params": {}})
    )
    assert reply is not None and reply.topic == "brain.reply" and "40%" in reply.data["text"]
    assert reply.data["action"] == "command.applied"

    none = orch._dispatch_intent(
        Envelope(topic="nlu.intent", source="blazend-nlu",
                 data={"intent": "unknown_intent", "language": "pl", "params": {}})
    )
    assert none is None
