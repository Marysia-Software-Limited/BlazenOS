"""Tier 0 — the depth-independent repo-root helper.

Guards against the fixed-``parents[N]`` bug: modules can move within the domain
tree without breaking repo-relative path math (the dev fallback for models/).
"""

from __future__ import annotations

from blazend._paths import repo_root


def test_repo_root_finds_monorepo():
    root = repo_root()
    # The marker the helper looks for, plus a sibling, must both be present.
    assert (root / "rpi5" / "pyproject.toml").is_file()
    assert (root / "configs").is_dir()


def test_models_root_uses_repo_when_env_unset(monkeypatch):
    from blazend.domains.local_ai.adapters.rpi5.localllm import models_root

    monkeypatch.delenv("BLAZEN_MODELS_DIR", raising=False)
    assert models_root() == repo_root() / "models"
