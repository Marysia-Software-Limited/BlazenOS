"""Tier 1 — blazend.config layered loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from blazend.config import ConfigLoader, load


@pytest.fixture
def repo_configs(monkeypatch: pytest.MonkeyPatch) -> Path:
    root = Path(__file__).resolve().parents[3] / "configs"
    monkeypatch.setenv("BLAZEN_CONFIG_ROOT", str(root))
    return root


def test_load_system(repo_configs: Path):
    cfg = ConfigLoader().load("system")
    assert cfg.get("version") == 1
    # Polish-only runtime (Decision 2026-06-27): EN deferred, assets retained.
    # See docs/13-LANGUAGES.md.
    assert cfg.get("languages.enabled") == ["pl"]
    assert cfg.get("ssh.enabled") is True   # on by default (pubkey-only); see docs/06
    assert cfg.get("telemetry.enabled") is False


def test_load_asr_default_multilingual(repo_configs: Path):
    cfg = load("asr")
    # 8 GB baked default is multilingual `small`; language pinned to Polish
    # (Polish-only runtime — EN deferred). See docs/13-LANGUAGES.md.
    assert cfg.get("active") == "small"
    assert cfg.get("language") == "pl"


def test_load_llm_engine_safe(repo_configs: Path):
    cfg = load("llm")
    # Default engine must not assume Hailo is present.
    assert cfg.get("active_engine") in {"auto", "cpu"}


def test_env_override_asr_model(repo_configs: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BLAZEN_ASR_MODEL", "small")
    cfg = load("asr")
    assert cfg.get("active") == "small"


def test_env_override_wake_words_list(repo_configs: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BLAZEN_WAKE_WORDS", "hey_blazen_en, hey_blazen_pl, alexa")
    cfg = load("wake-word")
    assert cfg.get("active") == ["hey_blazen_en", "hey_blazen_pl", "alexa"]


def test_require_missing_raises(repo_configs: Path):
    cfg = load("system")
    with pytest.raises(KeyError):
        cfg.require("nonexistent.deep.key")


def test_get_missing_returns_default(repo_configs: Path):
    cfg = load("system")
    assert cfg.get("nonexistent", "fallback") == "fallback"
