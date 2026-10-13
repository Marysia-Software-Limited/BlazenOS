"""quiet-until marker: naive and zoned ISO stamps both work (2026-10-12 fix)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blazend.domains.systems.adapters.rpi5.orchestrator.supervisor import _quiet_until_active


def test_zoned_stamp_in_the_future_is_quiet():
    stamp = (datetime.now(UTC) + timedelta(hours=1)).isoformat(timespec="seconds")
    assert _quiet_until_active(stamp)  # used to raise TypeError (naive vs aware)


def test_zoned_stamp_in_the_past_is_not_quiet():
    stamp = (datetime.now(UTC) - timedelta(hours=1)).isoformat(timespec="seconds")
    assert not _quiet_until_active(stamp)


def test_naive_stamp_still_works():
    assert _quiet_until_active((datetime.now() + timedelta(minutes=5)).isoformat())
    assert not _quiet_until_active((datetime.now() - timedelta(minutes=5)).isoformat())


def test_junk_raises_value_error():
    with pytest.raises(ValueError):
        _quiet_until_active("tomorrow-ish")
