"""Tier 0 — BLAZEN_CONFIG_ROOT seams for desktop installs (2026-08-23).

The appliance keeps /usr/share/blazen/defaults + /etc/blazen; a desktop
install layers two home-rooted dirs via a pathsep list, and the runtime
audio detector's device env vars must survive their embedded commas.
"""
from __future__ import annotations


def test_config_root_accepts_pathsep_list(tmp_path, monkeypatch):
    """Desktop installs (2026-08-23): BLAZEN_CONFIG_ROOT may be a pathsep list
    — defaults root then site root, later wins, overrides under the LAST."""
    import os as _os
    defaults, site = tmp_path / "defaults", tmp_path / "site"
    defaults.mkdir()
    site.mkdir()
    (defaults / "audio.yaml").write_text("version: 1\ninput:\n  device: default\nvolume: 10\n")
    (site / "audio.yaml").write_text("version: 1\nvolume: 55\n")
    monkeypatch.setenv("BLAZEN_CONFIG_ROOT", f"{defaults}{_os.pathsep}{site}")
    from blazend.config.loader import ConfigLoader
    cfg = ConfigLoader().load("audio")
    assert cfg.get("volume") == 55                      # site wins
    assert cfg.get("input.device") == "default"         # defaults survive


def test_device_env_values_with_commas_are_not_split(tmp_path, monkeypatch):
    """ALSA device strings contain commas — BLAZEN_INPUT_DEVICE must stay one
    string, never become a list (the _apply_env_overrides split trap)."""
    root = tmp_path / "cfg"
    root.mkdir()
    (root / "audio.yaml").write_text("version: 1\ninput:\n  device: default\n")
    monkeypatch.setenv("BLAZEN_CONFIG_ROOT", str(root))
    monkeypatch.setenv("BLAZEN_INPUT_DEVICE", "plughw:CARD=USB,DEV=0")
    from blazend.config.loader import ConfigLoader
    cfg = ConfigLoader().load("audio")
    assert cfg.get("input.device") == "plughw:CARD=USB,DEV=0"
