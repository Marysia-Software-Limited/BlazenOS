"""Tier 0 — the keyless RSS news fallback (offline; injected transport)."""
from __future__ import annotations

from datetime import UTC
from pathlib import Path

import pytest

from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.news import (
    NewsClient,
    NewsError,
    _decode_feed,
    _repair_mojibake,
    parse_items,
    parse_titles,
)

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _config_root(monkeypatch):
    # Real news.yaml has two pl feeds — needed by the fall-through test.
    monkeypatch.setenv("BLAZEN_CONFIG_ROOT", str(REPO / "configs"))

_RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>Example Feed</title>
  <item><title>Pierwsza wiadomość</title></item>
  <item><title>  Druga   wiadomość  </title></item>
  <item><title>Trzecia</title></item>
</channel></rss>"""

_ATOM = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Feed</title>
  <entry><title>First entry</title></entry>
  <entry><title>Second entry</title></entry>
</feed>"""


def test_parse_titles_rss_skips_channel_and_trims():
    titles = parse_titles(_RSS, limit=5)
    assert titles == ["Pierwsza wiadomość", "Druga wiadomość", "Trzecia"]


def test_parse_titles_atom():
    assert parse_titles(_ATOM, limit=5) == ["First entry", "Second entry"]


def test_parse_titles_respects_limit():
    assert parse_titles(_RSS, limit=2) == ["Pierwsza wiadomość", "Druga wiadomość"]


def test_parse_titles_rejects_malformed():
    with pytest.raises(NewsError):
        parse_titles("<not xml", limit=3)


def test_headlines_uses_configured_feed_per_language():
    calls: list[str] = []

    def transport(url: str) -> str:
        calls.append(url)
        return _RSS
    client = NewsClient(transport=transport)
    titles = client.headlines("pl", limit=2)
    assert titles == ["Pierwsza wiadomość", "Druga wiadomość"]
    assert calls  # a feed URL was fetched


def test_headlines_falls_through_to_next_feed_on_error():
    seen: list[str] = []

    def transport(url: str) -> str:
        seen.append(url)
        if len(seen) == 1:
            raise NewsError("first feed down")
        return _RSS
    # Default pl config has several feeds (local+national); first fails → next used.
    client = NewsClient(transport=transport)
    titles = client.headlines("pl")
    assert titles and len(seen) >= 2


# -- tiered collection --------------------------------------------------------

def test_by_tier_merges_and_dedupes_across_feeds():
    # The `local` tier (Kraków) has 3 configured feeds; two return the SAME
    # headline, one a different one → merged, de-duplicated.
    def transport(url: str) -> str:
        if "onet" in url:
            return ("<rss><channel><title>C</title>"
                    "<item><title>Ta sama wiadomość</title></item></channel></rss>")
        return ("<rss><channel><title>C</title>"
                "<item><title>Ta sama wiadomość</title></item>"
                "<item><title>Inna wiadomość</title></item></channel></rss>")
    client = NewsClient(transport=transport)
    titles = client.by_tier("local", limit=5)
    assert titles.count("Ta sama wiadomość") == 1  # de-duplicated across feeds
    assert "Inna wiadomość" in titles


def test_by_tier_skips_a_dead_feed():
    def transport(url: str) -> str:
        if "radiokrakow" in url:
            raise NewsError("radio down")
        return "<rss><channel><title>C</title><item><title>Żywa</title></item></channel></rss>"
    titles = NewsClient(transport=transport).by_tier("local")
    assert "Żywa" in titles  # one dead feed doesn't sink the tier


def test_collect_returns_all_requested_tiers():
    client = NewsClient(transport=lambda _u: _RSS)
    got = client.collect(["local", "national", "world"])
    assert set(got) == {"local", "national", "world"}
    assert all(got[t] for t in got)


_RICH_RSS = (
    "<rss><channel><title>Kanał</title>"
    "<item><title>Powódź w Małopolsce</title>"
    "<description>&lt;p&gt;Po nocnych ulewach &lt;b&gt;rzeka wystąpiła&lt;/b&gt; "
    "z brzegów. Ewakuowano 40 osób. Straty szacuje się na 2 mln zł.&lt;/p&gt;"
    "</description></item>"
    "<item><title>Sama wiadomość bez opisu</title></item>"
    "</channel></rss>"
)


def test_parse_items_extracts_and_strips_summaries():
    """The dictated brief (2026-09-11): items carry HTML-stripped summaries so
    the news can be TOLD, not just its headline read out."""
    items = parse_items(_RICH_RSS, 5)
    assert items[0]["title"] == "Powódź w Małopolsce"
    assert "rzeka wystąpiła z brzegów" in items[0]["summary"]
    assert "<" not in items[0]["summary"]  # HTML stripped
    assert items[1] == {"title": "Sama wiadomość bez opisu", "summary": "", "ts": None}


def test_rich_by_tier_dedupes_and_keeps_summaries():
    client = NewsClient(transport=lambda _u: _RICH_RSS)
    rich = client.rich_by_tier("local", limit=5)
    titles = [it["title"] for it in rich]
    assert titles.count("Powódź w Małopolsce") == 1
    assert rich[0]["summary"]


# -- recency + source diversity (2026-09-20: "chcę najnowsze, nie historyczne") --

def _rss_with_dates(*items: tuple[str, str]) -> str:
    body = "".join(
        f"<item><title>{t}</title><pubDate>{d}</pubDate></item>" if d
        else f"<item><title>{t}</title></item>"
        for t, d in items)
    return f"<rss><channel><title>C</title>{body}</channel></rss>"


def test_rich_by_tier_drops_stale_items_keeps_undated():
    """A pinned evergreen feature (months old) must not reach the brief; an
    item with no date at all still may (feeds without timestamps stay alive)."""
    from datetime import datetime, timedelta
    from email.utils import format_datetime

    now = datetime.now(UTC)
    feed = _rss_with_dates(
        ("Historyczny szpital — reportaż", format_datetime(now - timedelta(days=30))),
        ("Świeża wiadomość", format_datetime(now - timedelta(hours=2))),
        ("Bez daty", ""),
    )
    rich = NewsClient(transport=lambda _u: feed).rich_by_tier("local", limit=5)
    titles = [it["title"] for it in rich]
    assert "Świeża wiadomość" in titles
    assert "Bez daty" in titles
    assert "Historyczny szpital — reportaż" not in titles


def test_rich_by_tier_round_robins_across_feeds():
    """One source must not fill the whole tier: the top story of EVERY feed
    comes first (rank 0 across feeds), then the second stories, and so on."""
    def transport(url: str) -> str:
        if "radiokrakow" in url:
            return _rss_with_dates(("R1", ""), ("R2", ""))
        if "onet" in url:
            return _rss_with_dates(("O1", ""), ("O2", ""))
        return _rss_with_dates(("W1", ""), ("W2", ""))
    titles = NewsClient(transport=transport).by_tier("local", limit=4)
    assert titles == ["R1", "O1", "W1", "R2"]


def test_rich_by_tier_degrades_when_every_item_is_stale():
    """All-stale tier (broken feed clocks or a slow news day beyond the window)
    → better old news than none: the unfiltered items come back."""
    feed = _rss_with_dates(("Stara wiadomość", "Mon, 01 Jan 2024 10:00:00 +0000"))
    rich = NewsClient(transport=lambda _u: feed).rich_by_tier("local", limit=3)
    assert [it["title"] for it in rich] == ["Stara wiadomość"]


# -- charset sanitisation (2026-09-21: iso-8859-2 / cp1250 / cp1252 / cp852) --

def test_repair_mojibake_iso_8859_2_misread():
    """Radio Kraków (2026-09-20): valid UTF-8 whose content is ISO-8859-2
    misread as Latin-1 by the publisher — "wod±" is "wodą"."""
    assert (_repair_mojibake("Koniec problemów z wod± w Wielkiej Wsi")
            == "Koniec problemów z wodą w Wielkiej Wsi")


def test_repair_mojibake_cp1250_misread():
    assert _repair_mojibake("Koœció³ œw. Anny zamkniêty") == "Kościół św. Anny zamknięty"


def test_repair_mojibake_leaves_legitimate_text_alone():
    for text in ("Pound hits £1.30 against euro",
                 "Citroën prezentuje nowy model",
                 "Zwykły polski tytuł: żółta łódź"):
        assert _repair_mojibake(text) == text


def test_decode_feed_picks_the_right_legacy_code_page():
    """Raw non-UTF-8 bytes: the decoder must SCORE the candidates — "first
    code page that doesn't throw" would always pick cp1250 for iso/cp852."""
    pangram = "Zażółć gęślą jaźń w Krakowie"
    for enc in ("iso-8859-2", "cp1250", "cp852"):
        assert _decode_feed(pangram.encode(enc)) == pangram


def test_decode_feed_utf8_passthrough():
    assert _decode_feed("Świeża wiadomość".encode()) == "Świeża wiadomość"


def test_parse_items_repairs_titles_and_summaries():
    feed = ("<rss><channel><title>C</title>"
            "<item><title>Awaria wod± w Wieliczce</title>"
            "<description>Mieszkañcy bez wody do pi±tku.</description></item>"
            "</channel></rss>")
    it = parse_items(feed, 5)[0]
    assert it["title"] == "Awaria wodą w Wieliczce"
    assert it["summary"] == "Mieszkańcy bez wody do piątku."
