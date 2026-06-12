"""Tier 0 — intent dispatch + the voice-policy confirmation grammar (M5)."""
from __future__ import annotations

from blazend.dispatch import IntentDispatcher, SettingsStore

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
]}
POLICY = {
    "allow_voice_mutation": {
        "audio.volume": {"confirm": "never"},
        "system.power.reboot": {"confirm": "loud"},
        "system.factory_reset": {"confirm": "double_loud"},
        "llm.active_engine": {"confirm": "single", "allowed_values": ["auto", "cpu", "hailo"]},
    },
    "deny_voice_mutation": ["system.firewall.**"],
}


def _disp(tmp_path):
    return IntentDispatcher(INTENTS, POLICY, SettingsStore(tmp_path / "settings.json"))


def test_volume_mutate_no_confirm(tmp_path):
    d = _disp(tmp_path)
    r = d.dispatch("volume_up", {}, "pl")
    assert r.action == "applied" and r.data["value"] == 60 and "60%" in r.speak
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
    _disp(tmp_path).dispatch("volume_up", {}, "pl")  # 50 → 60
    assert _disp(tmp_path).dispatch("volume_up", {}, "pl").data["value"] == 70  # 60 → 70


def test_orchestrator_dispatches_nlu_intent(tmp_path):
    from blazend.events import Envelope
    from blazend.orchestrator.supervisor import Orchestrator

    orch = Orchestrator(runtime_dir_=tmp_path, dispatcher=_disp(tmp_path))
    reply = orch._dispatch_intent(
        Envelope(topic="nlu.intent", source="blazend-nlu",
                 data={"intent": "volume_up", "language": "pl", "params": {}})
    )
    assert reply is not None and reply.topic == "brain.reply" and "60%" in reply.data["text"]
    assert reply.data["action"] == "command.applied"

    none = orch._dispatch_intent(
        Envelope(topic="nlu.intent", source="blazend-nlu",
                 data={"intent": "unknown_intent", "language": "pl", "params": {}})
    )
    assert none is None
