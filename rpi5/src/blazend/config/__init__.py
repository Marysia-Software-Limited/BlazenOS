"""Shared YAML configuration loader.

Layered resolution (per docs/07-CONFIGURATION.md):
  1. /usr/share/blazen/defaults/<name>.yaml      (shipped)
  2. /etc/blazen/<name>.yaml                     (site)
  3. /etc/blazen/overrides/admin.yaml            (SSH path)
  4. /etc/blazen/overrides/voice.yaml            (voice path)
  5. BLAZEN_* env vars                           (build/test)

In dev-host mode (`BLAZEN_CONFIG_ROOT` set to the repo's `configs/`),
steps 1-2 collapse to the repo's tree so iteration doesn't need root.
"""

from .loader import Config, ConfigLoader, load

__all__ = ["Config", "ConfigLoader", "load"]
