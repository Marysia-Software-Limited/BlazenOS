"""Tier 1 — orchestrator wake gating.

With `require_wake`, a command (`nlu.intent`) is only acted on within the
conversation window opened by a `wake.detected`; otherwise Jessica stays silent.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from blazend.dispatch import DispatchResult
from blazend.events import Envelope, wake_detected
from blazend.orchestrator import Orchestrator


class _FakeDispatcher:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def dispatch(self, name: str, params: dict, language: str = "pl") -> DispatchResult:
        self.calls.append(name)
        return DispatchResult(speak="ok", language=language, action="tool")

    def pinned_language(self) -> str | None:
        return None


def _intent() -> Envelope:
    return Envelope(
        topic="nlu.intent",
        source="blazend-nlu",
        data={"intent": "what_time", "params": {}, "language": "pl", "transcript": "która godzina"},
    )


@pytest.fixture
def runtime_dir():
    d = Path(tempfile.mkdtemp(prefix="bl-wg-", dir="/tmp"))
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


@pytest.mark.asyncio
async def test_command_ignored_until_woken(runtime_dir: Path):
    fake = _FakeDispatcher()
    orch = Orchestrator(peers=(), runtime_dir_=runtime_dir, dispatcher=fake)
    orch._require_wake = True  # noqa: SLF001
    orch._wake_window_s = 20.0  # noqa: SLF001
    await orch._publisher.bind()  # noqa: SLF001
    try:
        # Asleep: a command is heard but not acted on.
        await orch._on_envelope("nlu", _intent())  # noqa: SLF001
        assert fake.calls == []

        # "Hej Jessico" opens the window → the next command dispatches.
        await orch._on_envelope(  # noqa: SLF001
            "wake", wake_detected(source="blazend-wake", model="jessica", score=0.9, language="pl")
        )
        await orch._on_envelope("nlu", _intent())  # noqa: SLF001
        assert fake.calls == ["what_time"]
    finally:
        await orch._publisher.close()  # noqa: SLF001
