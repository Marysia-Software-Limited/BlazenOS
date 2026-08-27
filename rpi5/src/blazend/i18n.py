"""Message catalog for every user-facing string on the Pi surface.

One YAML catalog — ``configs/phrases.yaml`` — holds all spoken/displayed text,
keyed by stable ids with per-language values, so external translators (or
agents) edit ONE file instead of chasing ~200 literals through the code.
Replaces the five copy-pasted ``_t(lang, pl, en)`` helpers (Decision
2026-08-27; inventory in docs/21-TRANSLATIONS.md).

Catalog shape (see ``configs/phrases.yaml``)::

    cues:     {listening: {pl: "Słucham?", en: "Yes?"}}          # v1, unchanged
    messages: {radio.station_on: {pl: "Włączam stację {station}.", en: …}}
    plurals:  {tracks: {pl: {one: …, few: …, many: …}, en: {one: …, other: …}}}
    tables:   {weather_codes: {"0": {pl: …, en: …}}}
    prompts:  {persona: {pl: …, en: …}}

Rules the catalog obeys (enforced by ``rpi5/tests/unit/test_i18n_catalog.py``):
every key carries BOTH ``pl`` and ``en`` (asset parity per docs/13-LANGUAGES.md
even though runtime is Polish-only), and placeholders match across languages.
Recognition data (intent trigger regexes, radio aliases) is NOT here — it
lives with its matchers and must never be machine-translated.

Loaded once per process through :func:`blazend.config.load`, so the usual
defaults→site layering applies: a site can override any single message in
``<site>/phrases.yaml`` without touching the shipped catalog.
"""

from __future__ import annotations

import logging
from typing import Any

from blazend.config import load

log = logging.getLogger("blazend.i18n")

# Polish-first: the fallback chain for a missing language is lang → en → pl.
_FALLBACK_LANGS = ("en", "pl")


def _plural_category(n: int, lang: str) -> str:
    """CLDR-ish plural category. Polish: one / few / many; English (and the
    default for anything else): one / other."""
    if lang == "pl":
        if n == 1:
            return "one"
        if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
            return "few"
        return "many"
    return "one" if n == 1 else "other"


class Catalog:
    """The parsed message catalog. Use the module-level helpers in app code;
    instantiate directly (with ``data=``) only in tests."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        if data is None:
            data = load("phrases").data
        self._messages: dict[str, Any] = dict(data.get("messages", {}) or {})
        # cues are v1 keys — expose them through the same lookup namespace.
        for key, val in (data.get("cues", {}) or {}).items():
            self._messages.setdefault(f"cues.{key}", val)
        for key, val in (data.get("prompts", {}) or {}).items():
            self._messages.setdefault(f"prompts.{key}", val)
        self._plurals: dict[str, Any] = dict(data.get("plurals", {}) or {})
        self._tables: dict[str, Any] = dict(data.get("tables", {}) or {})

    # ----- lookups --------------------------------------------------------

    def _pick(self, entry: Any, lang: str) -> Any:
        if not isinstance(entry, dict):
            return None
        if lang in entry:
            return entry[lang]
        for fb in _FALLBACK_LANGS:
            if fb in entry:
                return entry[fb]
        return None

    def msg(self, key: str, lang: str, **params: Any) -> str:
        """The message ``key`` in ``lang`` with ``params`` interpolated.
        Missing key/language degrades loudly-but-safely: en → pl → the key
        itself (logged) — the voice path must never crash over a string."""
        value = self._pick(self._messages.get(key), lang)
        if value is None:
            log.warning("i18n: missing message %r (lang=%s)", key, lang)
            return key
        try:
            return str(value).format(**params) if params else str(value)
        except (KeyError, IndexError) as exc:
            log.warning("i18n: bad placeholders for %r: %s", key, exc)
            return str(value)

    def plural(self, key: str, n: int, lang: str, **params: Any) -> str:
        """The plural form of ``key`` for count ``n``. The count is available
        to the template as ``{n}`` (plus any extra ``params``)."""
        entry = self._pick(self._plurals.get(key), lang)
        if not isinstance(entry, dict):
            log.warning("i18n: missing plural %r (lang=%s)", key, lang)
            return f"{n} {key}"
        cat = _plural_category(n, lang)
        value = entry.get(cat) or entry.get("other") or entry.get("many") \
            or next(iter(entry.values()), key)
        try:
            return str(value).format(n=n, **params)
        except (KeyError, IndexError) as exc:
            log.warning("i18n: bad placeholders for plural %r: %s", key, exc)
            return str(value)

    def table(self, name: str, key: Any, lang: str, default: str = "") -> str:
        """One entry of a lookup table (weather codes, day/month names)."""
        table = self._tables.get(name) or {}
        value = self._pick(table.get(str(key)), lang)
        if value is None:
            return default
        return str(value)


_CATALOG: Catalog | None = None


def catalog() -> Catalog:
    """Process-wide catalog, loaded once (strings change at install time)."""
    global _CATALOG  # noqa: PLW0603 — module cache, single-threaded asyncio
    if _CATALOG is None:
        _CATALOG = Catalog()
    return _CATALOG


def msg(key: str, lang: str, **params: Any) -> str:
    """Module-level shortcut for :meth:`Catalog.msg`."""
    return catalog().msg(key, lang, **params)


def plural(key: str, n: int, lang: str, **params: Any) -> str:
    """Module-level shortcut for :meth:`Catalog.plural`."""
    return catalog().plural(key, n, lang, **params)


def table(name: str, key: Any, lang: str, default: str = "") -> str:
    """Module-level shortcut for :meth:`Catalog.table`."""
    return catalog().table(name, key, lang, default)


__all__ = ["Catalog", "catalog", "msg", "plural", "table"]
