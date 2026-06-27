"""Keyless RSS news fallback — headlines when Gemini grounding is unavailable.

Gemini stays the *primary* news path (it can focus on Kraków + Poland from
international agencies and translate to Polish). When Gemini is absent or errors
(quota/billing), this provides a degraded but useful brief from plain RSS feeds
(``configs/news.yaml``) — no API key, no LLM. Polish feeds are already in Polish
(Poland-focused), so the ``pl`` brief needs no translation; ``en`` uses
international agencies. The HTTP ``transport`` is injectable for offline tests.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from collections.abc import Callable
from xml.etree import ElementTree as ET

from blazend.config import load

# A transport takes a URL and returns the raw feed text (GET).
Transport = Callable[[str], str]

_DEFAULT_FEEDS: dict[str, list[dict[str, str]]] = {
    "pl": [{"name": "RMF24", "url": "https://www.rmf24.pl/fakty/feed"}],
    "en": [{"name": "BBC World", "url": "https://feeds.bbci.co.uk/news/world/rss.xml"}],
}


class NewsError(RuntimeError):
    """Raised when no headlines could be fetched."""


def _http_get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "blazend/1.0", "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:  # noqa: S310 (configured hosts)
            text: str = resp.read().decode("utf-8", "replace")
            return text
    except urllib.error.HTTPError as e:  # pragma: no cover - network path
        raise NewsError(f"news HTTP {e.code}") from e
    except urllib.error.URLError as e:  # pragma: no cover - network path
        raise NewsError(f"news feed unreachable: {e.reason}") from e


def parse_titles(feed_xml: str, limit: int) -> list[str]:
    """Extract item/entry titles from RSS or Atom, skipping the channel title."""
    try:
        root = ET.fromstring(feed_xml)
    except ET.ParseError as e:
        raise NewsError(f"malformed feed: {e}") from e
    titles: list[str] = []
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]  # strip any namespace
        if tag in ("item", "entry"):
            for child in el:
                if child.tag.rsplit("}", 1)[-1] != "title":
                    continue
                txt = (child.text or "").strip()
                if txt:
                    titles.append(" ".join(txt.split()))
                    break
        if len(titles) >= limit:
            break
    return titles


class NewsClient:
    """Fetches recent headlines from the configured RSS feeds, per language."""

    def __init__(self, *, config_name: str = "news", transport: Transport | None = None) -> None:
        self._transport = transport or _http_get
        feeds, limit = dict(_DEFAULT_FEEDS), 5
        try:
            cfg = load(config_name)
            loaded = cfg.get("feeds", {}) or {}
            if loaded:
                feeds = {k: list(v) for k, v in loaded.items()}
            limit = int(cfg.get("max_headlines", limit))
        except Exception:  # noqa: BLE001 — no/unreadable config → built-in defaults
            pass
        self._feeds = feeds
        self._limit = limit

    def headlines(self, lang: str, limit: int | None = None) -> list[str]:
        """Top headlines for ``lang`` (falls back to the first feed that works)."""
        want = limit or self._limit
        feeds = self._feeds.get(lang) or self._feeds.get("en") or []
        last_error: Exception | None = None
        for feed in feeds:
            url = feed.get("url") if isinstance(feed, dict) else None
            if not url:
                continue
            try:
                titles = parse_titles(self._transport(url), want)
            except NewsError as e:
                last_error = e
                continue
            if titles:
                return titles[:want]
        if last_error is not None:
            raise last_error
        return []


__all__ = ["NewsClient", "NewsError", "parse_titles"]
