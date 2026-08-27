"""jessica_linux message catalog — the Linux-node CLI's user-facing strings.

Deliberately LOCAL (not ``blazend.i18n``): the mesh/media subcommands run on
nodes without the Pi appliance package (rachel), so this surface cannot import
blazend. Same contract as every catalog in docs/21-TRANSLATIONS.md: every key
carries BOTH ``pl`` and ``en`` (Polish-first; EN retained as an asset),
translators edit values only — never keys or ``{placeholders}``.
"""

from __future__ import annotations

_MESSAGES: dict[str, dict[str, str]] = {
    # -- CLI output (cli.py) --------------------------------------------------
    "catalog.stats": {
        "pl": "katalog: {books} książek ({merged} z sieci) → {path}",
        "en": "catalog: {books} books ({merged} from the mesh) → {path}",
    },
    "book.not_found": {
        "pl": "nie znalazłam książki: {query!r}",
        "en": "book not found: {query!r}",
    },
    "tts.missing_render": {
        "pl": "brak TTS w mesh — nie mogę renderować",
        "en": "no TTS in the mesh — can't render",
    },
    "tts.missing_read": {
        "pl": "brak TTS w mesh — nie mogę czytać",
        "en": "no TTS in the mesh — can't read",
    },
    "book.reading_from": {
        "pl": "Czytam „{title}” ({author}) od {start}{files}…",
        "en": "Reading “{title}” ({author}) from {start}{files}…",
    },
    "book.from_files": {
        "pl": " (z gotowych plików)",
        "en": " (from rendered files)",
    },
    "repl.banner": {
        "pl": "── Jessica (Linux node) ── Ctrl-D lub „wyjdź” kończy ──",
        "en": "── Jessica (Linux node) ── Ctrl-D or \"exit\" quits ──",
    },
    "repl.bye": {
        "pl": "\n  Jessica: Do usłyszenia!",
        "en": "\n  Jessica: See you!",
    },
    # -- audiobook render progress (books.py) --------------------------------
    "render.book_header": {
        "pl": "„{title}” — {author}: {total} rozdziałów → {dir}",
        "en": "“{title}” — {author}: {total} chapters → {dir}",
    },
    "render.chapter_file": {
        "pl": "  rozdział {i}/{total} → {name}",
        "en": "  chapter {i}/{total} → {name}",
    },
    "render.chapter": {
        "pl": "  rozdział {i}/{total}",
        "en": "  chapter {i}/{total}",
    },
    "render.done": {
        "pl": "gotowe: {n} rozdziałów w {dir}",
        "en": "done: {n} chapters in {dir}",
    },
    # -- spoken render summary (render_report.py) ----------------------------
    "render.summary": {
        "pl": "Audiobooki (literatura): gotowych {done}, w toku {rendering}, "
              "dziś +{today}, błędów {failed}{fails}.",
        "en": "Audiobooks (literatura): {done} done, {rendering} rendering, "
              "+{today} today, {failed} failed{fails}.",
    },
    "render.failword": {
        "pl": " — nie udało się: ",
        "en": " — failed: ",
    },
}


def msg(key: str, lang: str = "pl", **params: object) -> str:
    """The message ``key`` in ``lang`` (→ pl fallback → the key itself)."""
    entry = _MESSAGES.get(key, {})
    template = entry.get(lang) or entry.get("pl") or key
    try:
        return template.format(**params) if params else template
    except (KeyError, IndexError, ValueError):
        return template


__all__ = ["msg"]
