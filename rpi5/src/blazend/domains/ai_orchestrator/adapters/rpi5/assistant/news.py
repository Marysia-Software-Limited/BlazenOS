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
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any
from xml.etree import ElementTree as ET

from blazend.config import load

# A transport takes a URL and returns the decoded feed text (GET).
Transport = Callable[[str], str]

Feed = dict[str, str]
# A parsed item: {"title": str, "summary": str, "ts": float | None} — ts is the
# item's publication time (epoch) when the feed carries one, else None.
Item = dict[str, Any]

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


# Polish letters — the "this decode went well" signal for legacy code pages.
_POLISH = set("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ")
# Characters that are the CP1252/Latin-1 MISREADING of Polish bytes from a
# legacy code page (± = ą in ISO-8859-2, ¹/œ = ą/ś in CP1250, ¥ = ą in CP852,
# ³ = ł, ¶ = ś, ¿ = ż …). They essentially never occur in genuine news text,
# so any of them marks the item as mojibake to repair.
_MOJIBAKE_STRONG = set("±¹¶œŒ³¥¦¬¿")
# Misreadings that ALSO occur legitimately (£1bn, Citroën, ¡Hola!) — they count
# against a candidate's score but never trigger a repair on their own.
_MOJIBAKE_WEAK = set("êæñ£¯¼ŸÊÆÑ¡©ˆ")
# Legacy code pages Polish feeds have actually shipped (2026-09-21, user list:
# ISO-8859-2, CP1250/CP1252, CP852); decode candidates for non-UTF-8 bytes and
# re-decode candidates for the mojibake repair.
_LEGACY_PL = ("cp1250", "iso-8859-2", "cp852", "cp1252")


def _polish_score(text: str) -> int:
    """How Polish a decoding looks: diacritics score, misreadings penalise."""
    good = sum(ch in _POLISH for ch in text)
    strong = sum(ch in _MOJIBAKE_STRONG for ch in text)
    weak = sum(ch in _MOJIBAKE_WEAK for ch in text)
    return good - 2 * strong - weak


def _decode_feed(raw: bytes) -> str:
    """Decode feed bytes: UTF-8 first; otherwise the legacy Polish code page
    (ISO-8859-2 / CP1250 / CP852 / CP1252) whose result looks most Polish —
    these pages rarely *fail* to decode, so "first that works" would always
    pick the first one regardless of the actual charset."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    best, best_score = None, 0
    for enc in _LEGACY_PL:
        try:
            cand = raw.decode(enc)
        except UnicodeDecodeError:  # pragma: no cover — these pages are total
            continue
        s = _polish_score(cand)
        if best is None or s > best_score:
            best, best_score = cand, s
    return best if best is not None else raw.decode("iso-8859-2", "replace")


def _repair_mojibake(text: str) -> str:
    """Undo source-side mojibake: some feeds (Radio Kraków, 2026-09-20 "wod±")
    ship VALID UTF-8 whose content is a legacy code page misread as
    CP1252/Latin-1 by the publisher. Round-trip the text back to bytes and
    re-decode with each Polish code page; keep the best-scoring variant.
    Triggered only by strong markers, so legitimate £/ê/ñ stay untouched."""
    if not any(ch in _MOJIBAKE_STRONG for ch in text):
        return text
    best, best_score = text, _polish_score(text)
    for enc in ("cp1252", "latin-1"):
        try:
            raw = text.encode(enc)
        except UnicodeEncodeError:
            continue  # genuine Polish/other letters present — not a pure misread
        for dec in _LEGACY_PL:
            try:
                cand = raw.decode(dec)
            except UnicodeDecodeError:
                continue
            s = _polish_score(cand)
            if s > best_score:
                best, best_score = cand, s
    return best


def _http_get(url: str) -> str:
    """Fetch a feed and decode it (feeds vary in charset and some mislabel it —
    see :func:`_decode_feed`)."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 blazend/1.0",
                                               "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:  # noqa: S310 (configured hosts)
            raw: bytes = resp.read()
    except urllib.error.HTTPError as e:  # pragma: no cover - network path
        raise NewsError(f"news HTTP {e.code}") from e
    except urllib.error.URLError as e:  # pragma: no cover - network path
        raise NewsError(f"news feed unreachable: {e.reason}") from e
    return _decode_feed(raw)


# RSS/Atom child tags that carry the item body, in preference order.
_SUMMARY_TAGS = ("description", "summary", "encoded")  # encoded = content:encoded
# RSS/Atom child tags that carry the item's publication time.
_DATE_TAGS = ("pubDate", "published", "updated", "date")  # date = dc:date
_TAG_RE = re.compile(r"<[^>]+>")
_SUMMARY_MAX = 500  # chars per item — enough for 3-4 spoken sentences


def _parse_ts(txt: str) -> float | None:
    """A feed date (RFC 822 in RSS, ISO 8601 in Atom) as an epoch, else None."""
    try:
        return parsedate_to_datetime(txt).timestamp()
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(txt.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _strip_html(text: str) -> str:
    """Feed descriptions arrive as HTML — reduce to plain spoken text."""
    import html

    txt = _TAG_RE.sub(" ", html.unescape(text))
    return " ".join(txt.split())


def parse_items(feed_xml: str, limit: int) -> list[Item]:
    """Extract ``{"title", "summary", "ts"}`` per item/entry from RSS or Atom.

    The summary (description / summary / content:encoded, HTML-stripped,
    capped) is what lets the brief DICTATE the news instead of reading bare
    headlines (user request 2026-09-11). Items without a body get an empty
    summary — the caller degrades to the title. ``ts`` (pubDate / published /
    updated / dc:date as an epoch, else None) is what lets the tier keep only
    RECENT stories (2026-09-20) — feeds like Radio Kraków pin evergreen
    features on top, which used to hijack the brief."""
    try:
        root = ET.fromstring(feed_xml)
    except ET.ParseError as e:
        raise NewsError(f"malformed feed: {e}") from e
    items: list[Item] = []
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]  # strip any namespace
        if tag not in ("item", "entry"):
            continue
        title, summary = "", ""
        ts: float | None = None
        for child in el:
            ctag = child.tag.rsplit("}", 1)[-1]
            txt = (child.text or "").strip()
            if ctag == "title" and txt and not title:
                title = " ".join(txt.split())
            elif ctag in _SUMMARY_TAGS and txt and not summary:
                summary = _strip_html(txt)[:_SUMMARY_MAX]
            elif ctag in _DATE_TAGS and txt and ts is None:
                ts = _parse_ts(txt)
        if title:
            items.append({"title": _repair_mojibake(title),
                          "summary": _repair_mojibake(summary), "ts": ts})
        if len(items) >= limit:
            break
    return items


def parse_titles(feed_xml: str, limit: int) -> list[str]:
    """Extract item/entry titles from RSS or Atom, skipping the channel title."""
    return [str(it["title"]) for it in parse_items(feed_xml, limit)]


def _norm(title: str) -> str:
    """A comparison key for de-duping near-identical headlines across feeds."""
    return re.sub(r"[^0-9a-ząćęłńóśźż]+", " ", title.lower()).strip()


class NewsClient:
    """Fetches recent headlines from configured RSS feeds, grouped into tiers."""

    def __init__(self, *, config_name: str = "news", transport: Transport | None = None) -> None:
        self._transport = transport or _http_get
        tiers: dict[str, list[Feed]] = {k: list(v) for k, v in _DEFAULT_TIERS.items()}
        legacy: dict[str, list[Feed]] = {}
        per_tier, limit, max_age_h = 3, 5, 48.0
        try:
            cfg = load(config_name)
            loaded_tiers = cfg.get("tiers", {}) or {}
            if loaded_tiers:
                tiers = {k: [dict(f) for f in v] for k, v in loaded_tiers.items()}
            legacy = {k: [dict(f) for f in v] for k, v in (cfg.get("feeds", {}) or {}).items()}
            per_tier = int(cfg.get("max_per_tier", per_tier))
            limit = int(cfg.get("max_headlines", limit))
            max_age_h = float(cfg.get("max_age_h", max_age_h))
        except Exception:  # noqa: BLE001 — no/unreadable config → built-in defaults
            pass
        self._max_age_h = max_age_h
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
        return [str(it["title"]) for it in self.rich_by_tier(tier, limit)]

    def collect(self, tiers: Sequence[str], limit: int | None = None) -> dict[str, list[str]]:
        """Headlines for each requested tier → ``{tier: [titles]}`` (empty tiers kept)."""
        return {tier: self.by_tier(tier, limit) for tier in tiers}

    def _fresh_only(self, items: list[Item]) -> list[Item]:
        """Items no older than ``max_age_h`` (undated items are kept — a feed
        without timestamps must not go dark). ``max_age_h: 0`` disables."""
        if self._max_age_h <= 0:
            return items
        cutoff = time.time() - self._max_age_h * 3600.0
        return [it for it in items
                if it.get("ts") is None or float(it["ts"]) >= cutoff]

    def rich_by_tier(self, tier: str, limit: int | None = None) -> list[Item]:
        """Like :meth:`by_tier` but each item is ``{"title", "summary", "ts"}`` —
        the material for a DICTATED brief (2026-09-11), deduped by title.

        Selection (2026-09-20 — "słyszę o historycznym szpitalu, chcę
        najnowsze"): stale items (older than ``max_age_h``) are dropped, and
        the tier is filled ROUND-ROBIN across its feeds — every source's top
        story first — instead of letting the first configured feed (whose top
        slots may be pinned evergreen features) fill the whole tier."""
        want = limit or self._per_tier
        per_feed: list[list[Item]] = []
        for feed in self._tiers.get(tier, []):
            url = feed.get("url") if isinstance(feed, dict) else None
            if not url:
                continue
            try:
                per_feed.append(parse_items(self._transport(url), want))
            except NewsError:
                continue  # dead/malformed feed → skip, keep the tier alive
        fresh = [self._fresh_only(items) for items in per_feed]
        if not any(fresh):
            fresh = per_feed  # every feed stale/undated? broken clocks → degrade
        seen: set[str] = set()
        out: list[Item] = []
        for rank in range(max((len(f) for f in fresh), default=0)):
            for items in fresh:
                if rank >= len(items):
                    continue
                it = items[rank]
                key = _norm(str(it["title"]))
                if key and key not in seen:
                    seen.add(key)
                    out.append(it)
        return out[:want]

    def collect_rich(self, tiers: Sequence[str], limit: int | None = None,
                     ) -> dict[str, list[Item]]:
        """Rich items for each requested tier (empty tiers kept)."""
        return {tier: self.rich_by_tier(tier, limit) for tier in tiers}

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


__all__ = ["NewsClient", "NewsError", "parse_items", "parse_titles"]
