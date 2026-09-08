"""Tier 0 — uncertain-action confirmation dialog (2026-09-08): the answer
classifier, the confirmable-intent gate and the pending-resolution flow
(confirmed / denied / emotional / corrected / babble)."""

from __future__ import annotations

import asyncio
import types
from pathlib import Path

import pytest

from blazend.domains.systems.adapters.rpi5.orchestrator.supervisor import (
    Orchestrator,
    classify_confirmation,
)
from blazend.ipc import Envelope

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("BLAZEN_CONFIG_ROOT", str(REPO / "configs"))
    monkeypatch.setenv("BLAZEN_DATA_DIR", str(tmp_path))


def test_classify_confirmation_matrix():
    assert classify_confirmation("tak") == "confirmed"
    assert classify_confirmation("Tak, potwierdzam!") == "confirmed"
    assert classify_confirmation("zgadza się") == "confirmed"
    assert classify_confirmation("nie") == "denied"
    assert classify_confirmation("Nie, anuluj.") == "denied"
    assert classify_confirmation("zamknij się") == "emotional"
    assert classify_confirmation("spierdalaj") == "emotional"
    assert classify_confirmation("włącz radiową Trójkę") == "other"
    assert classify_confirmation("no i wtedy pojechaliśmy nad morze") == "other"


def test_confirmable_gate():
    stub = types.SimpleNamespace(
        _speaker_allow_any=frozenset({"stop_talking", "volume_up"}))
    ok = Orchestrator._confirmable
    assert ok(stub, "radio_play")
    assert ok(stub, "add_reminder")
    assert not ok(stub, "volume_up")  # any-voice safety set acts instantly
    assert not ok(stub, "wake_ack")
    assert not ok(stub, "voice_learn")  # dialog openers manage themselves


def _pending_stub(tmp_path) -> types.SimpleNamespace:
    stub = types.SimpleNamespace(
        _confirm_pending={
            "data": {"intent": "radio_play", "transcript": "włącz trójkę",
                     "language": "pl", "confidence": 0.55},
            "lang": "pl",
            "until": 1e12,  # far future — never stale in tests
        },
        _runtime_dir=tmp_path,
        spoken=[], learned=[], executed=[],
    )
    stub._clear_confirmation = (
        lambda: Orchestrator._clear_confirmation(stub))

    async def speak(text, lang=None):
        stub.spoken.append(text)

    stub._speak = speak
    stub._learn_event = (
        lambda outcome, text, data, corrected=None:
        stub.learned.append((outcome, corrected)))

    async def on_env(peer, env):
        stub.executed.append(env.data)

    stub._on_envelope = on_env
    return stub


def _miss(text: str) -> Envelope:
    return Envelope(topic="nlu.miss", source="blazend-nlu",
                    data={"transcript": text, "language": "pl"})


def _intent(name: str, text: str) -> Envelope:
    return Envelope(topic="nlu.intent", source="blazend-nlu",
                    data={"intent": name, "transcript": text, "language": "pl"})


def test_confirmed_executes_stored_intent(tmp_path):
    stub = _pending_stub(tmp_path)
    ok = asyncio.run(Orchestrator._resolve_confirmation(stub, _miss("tak")))
    assert ok and stub._confirm_pending is None
    assert stub.executed and stub.executed[0]["intent"] == "radio_play"
    assert stub.executed[0]["confidence"] == 1.0  # human-confirmed
    assert ("confirmed", None) in stub.learned


def test_bare_tak_arrives_as_wake_ack_intent(tmp_path):
    stub = _pending_stub(tmp_path)
    ok = asyncio.run(Orchestrator._resolve_confirmation(
        stub, _intent("wake_ack", "tak")))
    assert ok and stub.executed


def test_denied_cancels_with_ack(tmp_path):
    stub = _pending_stub(tmp_path)
    ok = asyncio.run(Orchestrator._resolve_confirmation(stub, _miss("nie")))
    assert ok and not stub.executed
    assert stub.spoken  # "Wycofuję."
    assert ("denied", None) in stub.learned


def test_emotional_cancels_with_apology(tmp_path):
    stub = _pending_stub(tmp_path)
    ok = asyncio.run(Orchestrator._resolve_confirmation(
        stub, _miss("zamknij się")))
    assert ok and not stub.executed
    assert any("rzepraszam" in s for s in stub.spoken)
    assert ("emotional", None) in stub.learned


def test_corrected_command_falls_through(tmp_path):
    stub = _pending_stub(tmp_path)
    ok = asyncio.run(Orchestrator._resolve_confirmation(
        stub, _intent("radio_play", "włącz dwójkę")))
    assert not ok  # caller processes the NEW command normally
    assert stub._confirm_pending is None
    assert ("corrected", "włącz dwójkę") in stub.learned


def test_babble_means_unconfirmed(tmp_path):
    stub = _pending_stub(tmp_path)
    ok = asyncio.run(Orchestrator._resolve_confirmation(
        stub, _miss("no i wtedy pojechaliśmy nad morze")))
    assert ok and not stub.executed and not stub.spoken  # silent cancel
    assert ("unconfirmed", None) in stub.learned
