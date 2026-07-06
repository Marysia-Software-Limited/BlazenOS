"""Extract chapter texts from an ebook (EPUB spine, or a converted .txt).

Two entry points:
- ``epub_to_chapters`` uses the EPUB spine (one document per spine item) via
  ``ebooklib``, stripping HTML with BeautifulSoup.
- ``text_to_chapters`` is the ``ebook-convert``-to-txt fallback: split on
  heading lines (ROZDZIAŁ / CZĘŚĆ / CHAPTER / PART), then hard-wrap any
  over-long chapter at ``max_chars`` on paragraph boundaries so a single TTS
  render call never gets an unbounded blob.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_HEADING = re.compile(
    r"^(ROZDZIA[ŁL]|CZ[ĘE][ŚS][ĆC]|CHAPTER|PART)\b.*$",
    re.IGNORECASE | re.MULTILINE,
)


@dataclass
class Chapter:
    index: int
    title: str
    text: str


def _wrap(title: str, body: str, max_chars: int, start: int) -> list[Chapter]:
    """Split ``body`` into <= max_chars chunks on paragraph boundaries."""
    paras = [p for p in re.split(r"\n\s*\n", body) if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paras:
        if buf and len(buf) + len(p) + 2 > max_chars:
            chunks.append(buf)
            buf = ""
        buf = f"{buf}\n\n{p}" if buf else p
    if buf:
        chunks.append(buf)
    if not chunks:
        chunks = [""]
    return [
        Chapter(index=start + i, title=title if i == 0 else f"{title} ({i + 1})",
                text=c.strip())
        for i, c in enumerate(chunks)
    ]


def text_to_chapters(txt: str, *, max_chars: int = 6000) -> list[Chapter]:
    marks = list(_HEADING.finditer(txt))
    if not marks:
        return _wrap("", txt.strip(), max_chars, 0)
    out: list[Chapter] = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(txt)
        title = m.group(0).strip()
        body = txt[m.end():end].strip()
        out.extend(_wrap(title, body, max_chars, len(out)))
    return out


def epub_to_chapters(epub_path: str, *, max_chars: int = 6000) -> list[Chapter]:
    from bs4 import BeautifulSoup  # noqa: PLC0415
    from ebooklib import ITEM_DOCUMENT, epub  # noqa: PLC0415

    book = epub.read_epub(epub_path)
    out: list[Chapter] = []
    for item in book.get_items_of_type(ITEM_DOCUMENT):
        soup = BeautifulSoup(item.get_content(), "html.parser")
        text = soup.get_text("\n").strip()
        if not text:
            continue
        heading = soup.find(["h1", "h2", "h3"])
        title = heading.get_text().strip() if heading else Path(item.get_name()).stem
        out.extend(_wrap(title, text, max_chars, len(out)))
    return out


__all__ = ["Chapter", "epub_to_chapters", "text_to_chapters"]
