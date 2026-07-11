"""Keyless RSS news — tiered headlines (Kraków / kraj / świat), Polish-first.

The data always comes from real RSS feeds (``configs/news.yaml``) — no API key,
no LLM — so the news brief satisfies the on-device contract. Feeds are grouped
into **tiers**:

- ``local`` — Kraków (Radio Kraków, Onet Kraków, Wyborcza Kraków): Polish;
- ``national`` — Poland (PAP, Onet, TVN24, Polsat): Polish;
- ``world`` — international agencies (Guardian, BBC, CNN, AP): **English** — the
  cloud composer in :mod:`...tools` translates these to Polish when a key is
  present;
- ``world_pl`` — Polish-language world coverage (Onet/TVN24/Polsat świat): the
  **keyless floor** for the world tier, so the brief stays fully Polish offline.

:meth:`NewsClient.collect` merges every feed in a tier, de-duplicates near-identical
titles, and caps the count. :meth:`NewsClient.headlines` (per-language, first
working feed) is kept for the older single-feed callers. The HTTP ``transport`` is
injectable so tests run fully offline. Real feeds that mislabel their charset
(Radio Kraków declares UTF-8 but ships CP1250) are decoded with a legacy fallback.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Sequence
from xml.etree import ElementTree as ET

from blazend.config import load

# A transport takes a URL and returns the decoded feed text (GET).
Transport = Callable[[str], str]

Feed = dict[str, str]

# Built-in defaults (used only when news.yaml is missing/unreadable). Real feeds
# live in configs/news.yaml; these keep the keyless floor alive with no config.
_DEFAULT_TIERS: dict[str, list[Feed]] = {
    "local": [{"name": "Onet Kraków", "url": "https://wiadomosci.onet.pl/krakow.feed"}],
    "national": [{"name": "Onet kraj", "url": "https://wiadomosci.onet.pl/kraj.feed"}],
    "world": [{"name": "BBC World", "url": "https://feeds.bbci.co.uk/news/world/rss.xml"}],
    "world_pl": [{"name": "Onet świat", "url": "https://wiadomosci.onet.pl/swiat.feed"}],
}


class NewsError(RuntimeError):
    """Raised when no headlines could be fetched."""


def _http_get(url: str) -> str:
    """Fetch a feed and decode it. Feeds vary in charset and some mislabel it
    (Radio Kraków declares UTF-8 but ships ISO-8859-2), so decode UTF-8 strict
    first and fall back to ISO-8859-2 (the standard legacy Polish RSS charset,
    where ``ą``/``ę`` differ from CP1250) before giving up with replacement."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 blazend/1.0",
                                               "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:  # noqa: S310 (configured hosts)
            raw: bytes = resp.read()
    except urllib.error.HTTPError as e:  # pragma: no cover - network path
        raise NewsError(f"news HTTP {e.code}") from e
    except urllib.error.URLError as e:  # pragma: no cover - network path
        raise NewsError(f"news feed unreachable: {e.reason}") from e
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("iso-8859-2", "replace")


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


def _norm(title: str) -> str:
    """A comparison key for de-duping near-identical headlines across feeds."""
    return re.sub(r"[^0-9a-ząćęłńóśźż]+", " ", title.lower()).strip()


def _dedupe(titles: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for t in titles:
        key = _norm(t)
        if key and key not in seen:
            seen.add(key)
            out.append(t)
    return out


class NewsClient:
    """Fetches recent headlines from configured RSS feeds, grouped into tiers."""

    def __init__(self, *, config_name: str = "news", transport: Transport | None = None) -> None:
        self._transport = transport or _http_get
        tiers: dict[str, list[Feed]] = {k: list(v) for k, v in _DEFAULT_TIERS.items()}
        legacy: dict[str, list[Feed]] = {}
        per_tier, limit = 3, 5
        try:
            cfg = load(config_name)
            loaded_tiers = cfg.get("tiers", {}) or {}
            if loaded_tiers:
                tiers = {k: [dict(f) for f in v] for k, v in loaded_tiers.items()}
            legacy = {k: [dict(f) for f in v] for k, v in (cfg.get("feeds", {}) or {}).items()}
            per_tier = int(cfg.get("max_per_tier", per_tier))
            limit = int(cfg.get("max_headlines", limit))
        except Exception:  # noqa: BLE001 — no/unreadable config → built-in defaults
            pass
        self._tiers = tiers
        # Per-language feed list for the legacy headlines() API: explicit `feeds:`
        # if present, else synthesised from the tiers (pl = home tiers, en = world).
        self._feeds = legacy or {
            "pl": tiers.get("local", []) + tiers.get("national", []) + tiers.get("world_pl", []),
            "en": tiers.get("world", []),
        }
        self._per_tier = per_tier
        self._limit = limit

    # -- tiered API --------------------------------------------------------
    def by_tier(self, tier: str, limit: int | None = None) -> list[str]:
        """Merged, de-duplicated headlines across every feed in ``tier``.

        Each feed contributes; a failing feed is skipped (never fatal), so a tier
        is resilient to one dead source. Returns up to ``limit`` (``max_per_tier``)."""
        want = limit or self._per_tier
        collected: list[str] = []
        for feed in self._tiers.get(tier, []):
            url = feed.get("url") if isinstance(feed, dict) else None
            if not url:
                continue
            try:
                collected.extend(parse_titles(self._transport(url), want))
            except NewsError:
                continue  # dead/malformed feed → skip, keep the tier alive
        return _dedupe(collected)[:want]

    def collect(self, tiers: Sequence[str], limit: int | None = None) -> dict[str, list[str]]:
        """Headlines for each requested tier → ``{tier: [titles]}`` (empty tiers kept)."""
        return {tier: self.by_tier(tier, limit) for tier in tiers}

    # -- legacy per-language API (older single-feed callers) ---------------
    def headlines(self, lang: str, limit: int | None = None) -> list[str]:
        """Top headlines for ``lang`` (first feed that works)."""
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
