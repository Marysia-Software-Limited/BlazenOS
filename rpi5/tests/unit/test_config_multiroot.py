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


def test_asr_idle_unload_frees_backend(tmp_path, monkeypatch):
    """GPU power saving (2026-08-23): unload_after_s > 0 drops the idle model;
    0 (the appliance default) never unloads."""

    root = tmp_path / "cfg"
    root.mkdir()
    (root / "asr.yaml").write_text("version: 1\nactive: small\nunload_after_s: 1\n")
    monkeypatch.setenv("BLAZEN_CONFIG_ROOT", str(root))
    from blazend.domains.voice_input.adapters.rpi5.asr.engine import Transcriber

    class _Fake:
        def run(self, pcm, forced):  # noqa: ANN001, ANN202
            class _R:
                text, language, avg_logprob = "ok", "pl", -0.1
            return _R()

    t = Transcriber(backend=_Fake())
    assert t.maybe_unload() is False          # never used yet → nothing to drop
    import numpy as np
    t.transcribe(np.zeros(16000, dtype=np.int16))
    assert t.maybe_unload() is False          # just used → stays loaded
    t._last_used -= 2                          # simulate 2 s of idleness
    assert t.maybe_unload() is True            # dropped
    assert t._backend is None
    # Appliance default: unload_after_s 0 → never unloads.
    (root / "asr.yaml").write_text("version: 1\nactive: small\n")
    t2 = Transcriber(backend=_Fake())
    t2.transcribe(np.zeros(16000, dtype=np.int16))
    t2._last_used -= 10_000
    assert t2.maybe_unload() is False
