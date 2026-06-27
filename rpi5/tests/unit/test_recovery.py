"""Tier 0 — recovery policy (health.status → LED + bilingual cue) for M5 fail-modes."""
from __future__ import annotations

import pytest

from blazend.domains.systems.adapters.rpi5.led import GREEN, RED, YELLOW, color_for
from blazend.domains.systems.adapters.rpi5.recovery import for_level
from blazend.events import Envelope, health_status


def test_for_level_polish_first_default():
    # No language given → Polish (system default is Polish-first).
    assert for_level("degraded").speak.startswith("Coś się zacięło")
    assert for_level("recovery").color == RED
    assert for_level("recovery").speak.startswith("Tryb awaryjny")
    crit = for_level("critical")
    assert crit.color == RED and crit.recovery_image is True
    assert crit.speak.startswith("Błąd krytyczny")


def test_for_level_english_counterpart():
    assert "i'm stuck" in for_level("degraded", "en").speak.lower()
    assert "recovery mode" in for_level("recovery", "en").speak.lower()
    assert "critical error" in for_level("critical", "en").speak.lower()


def test_for_level_colors_and_ok():
    assert for_level("degraded").color == YELLOW
    ok = for_level("ok")
    assert ok.color == GREEN and ok.speak == "" and ok.recovery_image is False


def test_led_maps_health_status():
    assert color_for("health.status", {"level": "degraded"}) == YELLOW
    assert color_for("health.status", {"level": "recovery"}) == RED
    assert color_for("health.status", {"level": "critical"}) == RED
    assert color_for("health.status", {"level": "ok"}) == GREEN
    assert color_for("health.status", {"level": "weird"}) is None


class _FakePublisher:
    def __init__(self) -> None:
        self.published: list[Envelope] = []

    async def publish(self, env: Envelope) -> None:
        self.published.append(env)


@pytest.mark.asyncio
async def test_orchestrator_announces_recovery(tmp_path):
    from blazend.domains.systems.adapters.rpi5.orchestrator.supervisor import Orchestrator

    orch = Orchestrator(runtime_dir_=tmp_path, dispatcher=None)
    orch._publisher = _FakePublisher()  # type: ignore[assignment]
    orch._default_lang = "pl"

    env = health_status(source="blazend-health", level="recovery",
                        unit="blazend-audio-in", action="recovery_mode")
    recovery = await orch._announce_recovery(env)

    assert recovery == {
        "level": "recovery", "unit": "blazend-audio-in",
        "action": "recovery_mode", "recovery_image": False,
    }
    spoken = orch._publisher.published  # type: ignore[attr-defined]
    assert len(spoken) == 1 and spoken[0].topic == "brain.reply"
    assert spoken[0].data["language"] == "pl"
    assert "awaryjny" in spoken[0].data["text"].lower()

    # The healthy case neither speaks nor records recovery state.
    assert await orch._announce_recovery(health_status(
        source="blazend-health", level="ok", unit="system")) is None
    assert len(orch._publisher.published) == 1  # type: ignore[attr-defined]
