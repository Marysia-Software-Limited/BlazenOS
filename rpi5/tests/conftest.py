"""Shared test baseline for the Pi 5 surface.

Tests run against the REPO's ``configs/`` tree unless a test (or the caller's
environment) points ``BLAZEN_CONFIG_ROOT`` elsewhere — the message catalog
(``blazend.i18n``) and every ``blazend.config.load`` consumer need a config
root to resolve strings; without one, an unset dev box would fall back to
``/usr/share/blazen`` + ``/etc/blazen`` and the catalog would come up empty
(messages degrade to their keys, failing phrase assertions confusingly).

Also resets the process-wide i18n catalog cache between tests so a test that
monkeypatches the config root never leaks its catalog into the next test.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

os.environ.setdefault("BLAZEN_CONFIG_ROOT", str(REPO_ROOT / "configs"))


@pytest.fixture(autouse=True)
def _reset_i18n_cache():
    import blazend.i18n as i18n

    i18n._CATALOG = None
    yield
    i18n._CATALOG = None
