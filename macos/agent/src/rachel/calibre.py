"""Read a Calibre library's metadata.db and locate Polish ebooks + their files.

Calibre stores its catalogue in ``<library>/metadata.db`` (sqlite) and each
book's files under ``<library>/<book.path>/``. This module reads that DB
read-only, filters to Polish (``language = pol``), and resolves a spoken/typed
query to a book. Slug is the stable ``calibre-<id>`` so a rendered audiobook
keeps its identity across catalogue rebuilds.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from audiobook_catalog.text_norm import _stem_phrase


def _tokens(text: str) -> set[str]:
    return {t for t in _stem_phrase(text).split() if t}


@dataclass
class CalibreBook:
    id: int
    title: str
    author: str
    language: str
    path: str                      # book dir relative to the library root
    formats: list[str]             # e.g. ["EPUB", "MOBI", "PDF"]
    _title: set[str] = field(default_factory=set, repr=False)

    @property
    def slug(self) -> str:
        return f"calibre-{self.id}"

    def format_file(self, library_root: str, fmt: str) -> Path | None:
        """Absolute path to the on-disk file for a given format (EPUB/MOBI/...)."""
        d = Path(library_root) / self.path
        for f in sorted(d.glob(f"*.{fmt.lower()}")):
            return f
        return None


class CalibreLibrary:
    def __init__(self, *, db_path: str, library_root: str) -> None:
        self.db_path = db_path
        self.library_root = library_root

    def polish_books(self) -> list[CalibreBook]:
        return self._query("pol")

    def _query(self, lang: str) -> list[CalibreBook]:
        con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """
            SELECT b.id, b.title, b.path,
                   COALESCE(a.name, 'Nieznany') AS author,
                   l.lang_code AS lang
            FROM books b
            JOIN books_languages_link bll ON b.id = bll.book
            JOIN languages l ON bll.lang_code = l.id
            LEFT JOIN books_authors_link bal ON b.id = bal.book
            LEFT JOIN authors a ON bal.author = a.id
            WHERE l.lang_code = ?
            GROUP BY b.id
            ORDER BY b.title
            """,
            (lang,),
        ).fetchall()
        out: list[CalibreBook] = []
        for r in rows:
            fmts = [x[0] for x in con.execute(
                "SELECT format FROM data WHERE book = ?", (r["id"],)).fetchall()]
            out.append(CalibreBook(
                id=r["id"], title=r["title"], author=r["author"], language=r["lang"],
                path=r["path"], formats=fmts, _title=_tokens(r["title"])))
        con.close()
        return out

    def resolve(self, query: str) -> CalibreBook | None:
        """Best Polish-book match for ``query`` across the title (longest overlap)."""
        q = _tokens(query)
        if not q:
            return None
        best, best_score = None, 0
        for b in self.polish_books():
            score = (100 if q <= b._title else 0) + len(q & b._title)
            if score > best_score:
                best, best_score = b, score
        return best if best_score else None


__all__ = ["CalibreBook", "CalibreLibrary"]
