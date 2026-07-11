"""Unit tests for the nightly render-manifest → fabric-note summary (pure, no I/O)."""
from __future__ import annotations

from datetime import datetime

from jessica_linux.render_report import summarize

_NOW = datetime(2026, 7, 11, 3, 30, 0)


def _manifest() -> dict[str, dict]:
    return {
        "calibre-1": {"title": "Lalka", "status": "done", "updated": "2026-07-11T01:10:00"},
        "calibre-2": {"title": "Pan Tadeusz", "status": "done", "updated": "2026-07-09T22:00:00"},
        "calibre-3": {"title": "Chłopi", "status": "rendering", "updated": "2026-07-11T03:00:00"},
        "calibre-4": {"title": "Ferdydurke", "status": "failed", "updated": "2026-07-11T02:00:00",
                      "error": "boom"},
    }


def test_id_is_stable_per_day() -> None:
    note_id, _ = summarize(_manifest(), now=_NOW)
    assert note_id == "render-2026-07-11"


def test_pl_counts_and_today() -> None:
    _, text = summarize(_manifest(), now=_NOW, lang="pl")
    assert "gotowych 2" in text          # two done total
    assert "w toku 1" in text            # one rendering
    assert "dziś +1" in text             # only Lalka finished on 2026-07-11
    assert "błędów 1" in text
    assert "Ferdydurke" in text          # failed title named


def test_en_parity() -> None:
    _, text = summarize(_manifest(), now=_NOW, lang="en")
    assert "2 done" in text
    assert "1 rendering" in text
    assert "+1 today" in text
    assert "1 failed" in text
    assert "Ferdydurke" in text


def test_no_failures_omits_fail_clause() -> None:
    m = {k: v for k, v in _manifest().items() if v["status"] != "failed"}
    _, text = summarize(m, now=_NOW, lang="pl")
    assert "błędów 0" in text
    assert "nie udało się" not in text


def test_empty_manifest() -> None:
    note_id, text = summarize({}, now=_NOW, lang="pl")
    assert note_id == "render-2026-07-11"
    assert "gotowych 0" in text


def test_many_failures_truncated_with_overflow() -> None:
    m = {f"c{i}": {"title": f"Book {i}", "status": "failed", "updated": "2026-07-11T00:00:00"}
         for i in range(8)}
    _, text = summarize(m, now=_NOW, lang="pl", max_fail_titles=3)
    assert "błędów 8" in text
    assert "(+5)" in text                # 8 failed, 3 named, +5 overflow
