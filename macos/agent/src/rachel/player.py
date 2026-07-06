"""Drive the `rachel-player` binary for chapter playback with resume.

Mirrors how the Pi orchestrator uses `blazend-player`: the player plays ONE
chapter file and writes a ``{"seconds","done"}`` position file; this module loops
chapters, maps that position onto the shared ``AudiobookProgress`` (chapter +
offset_s), and auto-advances. A mid-chapter stop (Ctrl-C) leaves the last
periodic position so the next ``resume`` picks up there.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

_REPO_PLAYER = Path(__file__).resolve().parents[3] / "player/target/release/rachel-player"


def player_binary() -> str:
    return os.environ.get("RACHEL_PLAYER_BIN", str(_REPO_PLAYER))


def play_book_argv(binary: str, chapter: str, *, start_seconds: float = 0.0,
                   position_file: str) -> list[str]:
    """The exact `rachel-player` argv for one chapter (speech → compressor on)."""
    argv = [binary, chapter]
    if start_seconds > 0:
        argv += ["--start-seconds", f"{start_seconds:.1f}"]
    argv += ["--position-file", position_file, "--compress"]
    return argv


def read_position(path: str) -> tuple[float, bool]:
    """Return ``(seconds, done)`` from a player position file, or ``(0.0, False)``."""
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return float(d.get("seconds", 0.0)), bool(d.get("done", False))
    except (OSError, ValueError):
        return 0.0, False


def latest_slug(progress_path: str) -> str | None:
    """The slug with the most recent ``updated`` timestamp, or None."""
    try:
        d = json.loads(Path(progress_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not d:
        return None
    return max(d, key=lambda s: d[s].get("updated", ""))


def play_book(chapters: list[str], slug: str, title: str, *, progress, now: str,
              start_chapter: int = 0, start_seconds: float = 0.0,
              binary: str | None = None) -> None:
    """Play ``chapters`` from ``start_chapter``/``start_seconds``, updating progress.

    Auto-advances on natural chapter end; a mid-chapter interrupt saves the resume
    point and stops. ``now`` is a caller-supplied timestamp (this module has no clock).
    """
    binary = binary or player_binary()
    pos_file = str(Path(progress._path).with_name(f".{slug}.pos.json"))  # noqa: SLF001
    ch = max(0, start_chapter)
    offset = max(0.0, start_seconds)
    try:
        while ch < len(chapters):
            argv = play_book_argv(binary, chapters[ch], start_seconds=offset,
                                  position_file=pos_file)
            subprocess.run(argv, check=False)
            seconds, done = read_position(pos_file)
            if done:
                ch += 1
                offset = 0.0
                if ch < len(chapters):
                    progress.save(slug, chapter=ch, offset_s=0.0, title=title, updated=now)
                else:
                    progress.clear(slug)  # finished the book
            else:
                progress.save(slug, chapter=ch, offset_s=seconds, title=title, updated=now)
                break
    except KeyboardInterrupt:
        seconds, _ = read_position(pos_file)
        progress.save(slug, chapter=ch, offset_s=seconds, title=title, updated=now)
    finally:
        Path(pos_file).unlink(missing_ok=True)


__all__ = ["latest_slug", "play_book", "play_book_argv", "player_binary", "read_position"]
