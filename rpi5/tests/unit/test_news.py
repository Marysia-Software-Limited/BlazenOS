"""Tier 0 — the keyless RSS news fallback (offline; injected transport)."""
from __future__ import annotations

from pathlib import Path

import pytest

from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.news import (
    NewsClient,
    NewsError,
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
    # Default pl config has two feeds (RMF24, Polsat); first fails → second used.
    client = NewsClient(transport=transport)
    titles = client.headlines("pl")
    assert titles and len(seen) >= 2
