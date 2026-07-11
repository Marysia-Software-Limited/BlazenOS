"""Summarise the literatura batch-render manifest into a one-line fabric note.

The long-haul render batch (``scripts/render-literatura.py``) keeps a per-book
manifest (``render-literatura.json``: ``{slug: {title, status, updated, ...}}``).
This turns that manifest into a short, **dated** note so the constellation — and
the operator over voice ("co słychać z audiobookami?") — can see how the render is
going without reading logs on paul. The note lands in the node's shared context
(``memory.json`` → fabric), where the Pi (authoritative) retains it.

Pure data → one function, no I/O. The nightly wrapper (``scripts/render-summary.py``)
resolves the manifest + ``memory.json`` and persists the note; the systemd timer
(``linux/systemd/render-summary.timer``) fires it once a night.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

Item = dict[str, Any]

# Runtime is Polish-only; the EN template is retained at the asset level so the
# note is re-enableable in English by a config flip (see docs/13-LANGUAGES.md).
_TMPL = {
    "pl": ("Audiobooki (literatura): gotowych {done}, w toku {rendering}, "
           "dziś +{today}, błędów {failed}{fails}."),
    "en": ("Audiobooks (literatura): {done} done, {rendering} rendering, "
           "+{today} today, {failed} failed{fails}."),
}
_FAILWORD = {"pl": " — nie udało się: ", "en": " — failed: "}


def summarize(manifest: dict[str, Item], *, now: datetime, lang: str = "pl",
              max_fail_titles: int = 5) -> tuple[str, str]:
    """Return ``(note_id, text)`` describing the manifest's state as of ``now``.

    ``note_id`` is stable per calendar day (``render-YYYY-MM-DD``) so re-running the
    summary the same day **replaces** the note rather than piling up duplicates
    (notes union by id across the fabric). ``today`` counts books whose ``done``
    timestamp falls on ``now``'s date; failures name up to ``max_fail_titles`` books.
    """
    day = now.date().isoformat()
    values = list(manifest.values())
    done = sum(1 for v in values if v.get("status") == "done")
    rendering = sum(1 for v in values if v.get("status") == "rendering")
    failed = [v for v in values if v.get("status") == "failed"]
    done_today = sum(1 for v in values
                     if v.get("status") == "done" and str(v.get("updated", "")).startswith(day))

    fails = ""
    if failed:
        titles = [str(v.get("title", "?")) for v in failed][:max_fail_titles]
        extra = len(failed) - len(titles)
        joined = ", ".join(titles) + (f" (+{extra})" if extra > 0 else "")
        fails = _FAILWORD.get(lang, _FAILWORD["pl"]) + joined

    tmpl = _TMPL.get(lang, _TMPL["pl"])
    text = tmpl.format(done=done, rendering=rendering, today=done_today,
                       failed=len(failed), fails=fails)
    return f"render-{day}", text
