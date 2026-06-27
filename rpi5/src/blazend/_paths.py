"""Shared filesystem path helpers (the platform layer).

Depth-independent so modules can move within the domain tree without breaking
repo-relative path math (the bug that a fixed ``parents[N]`` index causes).
"""

from __future__ import annotations

from pathlib import Path


def repo_root() -> Path:
    """The monorepo root — the directory containing ``rpi5/pyproject.toml``.

    Used only as the **dev fallback** for locating ``models/`` and dev binaries
    when ``BLAZEN_MODELS_DIR`` (and friends) are unset; the appliance image
    always sets those, so this is never hit in production. When the package is
    installed flat (e.g. ``/usr/lib/blazen/blazend``) there is no repo marker, so
    we return the install parent as a harmless best-effort.
    """
    here = Path(__file__).resolve()
    for anc in here.parents:
        if (anc / "rpi5" / "pyproject.toml").is_file():
            return anc
    return here.parents[1]
