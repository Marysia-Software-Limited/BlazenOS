# macOS (rachel) Calibre→Apple-TTS Audiobooks — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** From the Mac, turn a Polish Calibre ebook into a chapterized audiobook rendered with Apple on-device TTS, cataloged in the shared schema, and playable on the Mac with resume + chapter auto-advance — reusing the Pi's engine via new `domains/` common libs.

**Architecture:** Extract device-independent audiobook logic into two shared libs under `domains/` — `domains/audiobook-catalog/` (Python: catalog model + resolver + progress) and `domains/blazend-audiobook/` (Rust: portable player engine behind an `AudioSink` trait, no ALSA). The Mac agent (`macos/agent/`, Python) drives Calibre extraction + Apple/Azure TTS + catalog writes; the Mac player (`macos/player/`, Rust) links the shared engine + a CoreAudio/cpal sink. The Pi imports the same Python lib and (Phase C, separate plan) links the same Rust engine.

**Tech Stack:** Python 3.11+ (`ebooklib`, `beautifulsoup4`, stdlib sqlite3, `pytest`); macOS `say` + `ffmpeg`/`lame`; Rust (`symphonia`, `cpal`, `clap`); Calibre CLI (`ebook-convert`).

## Global Constraints

- **Polish-first:** filter `language = pol`; default TTS voice is pl-PL (`Zosia`). Verbatim config default: `voice = "Zosia"`.
- **On-device default, cloud opt-in:** Apple `say` is the default renderer. Azure (`pl-PL-MarekNeural`) only when `--premium` is passed; key read from `macos/.secrets.env` (`AZURE_SPEECH_KEY`), which is **gitignored** and never printed.
- **No secrets/models/media committed:** `.secrets.env`, `*.aiff`, `*.mp3`, rendered audiobook dirs, and `~/calibre` are never staged.
- **Always `domains/` for common code:** no copy-paste across platform adapters; shared logic lives in `domains/` and is imported/linked.
- **Don't break `jessica`:** the Pi appliance never depends on rachel. The Python extraction leaves Pi runtime byte-for-byte unchanged (thin re-export shims); Pi unit tests are the gate.
- **Stable slug:** Calibre books use `slug = f"calibre-{book_id}"`.
- **Catalog schema (verbatim):** `{"version": 1, "books": [ {"author","title","slug","genre","epoch","downloaded","chapters":[paths],"n_chapters", "source","language","voice","premium"} ]}`.
- **Progress schema (verbatim):** `{ slug: {"chapter": int, "offset_s": float, "title": str, "updated": str} }`.
- **Mac paths:** audiobook root `~/Library/Application Support/blazen/audiobooks/<slug>/NN.mp3`; `catalog.json` + `progress.json` in that root's parent (`~/Library/Application Support/blazen/audiobooks/`).

---

# PHASE A — Python: shared catalog lib + Mac render pipeline

## Task A1: Create `domains/audiobook-catalog/` package with text-normalization helpers

**Files:**
- Create: `domains/audiobook-catalog/pyproject.toml`
- Create: `domains/audiobook-catalog/src/audiobook_catalog/__init__.py`
- Create: `domains/audiobook-catalog/src/audiobook_catalog/text_norm.py`
- Test: `domains/audiobook-catalog/tests/test_text_norm.py`

**Interfaces:**
- Produces: `text_norm._fold(str)->str`, `text_norm._stem_token(str)->str`, `text_norm._stem_phrase(str)->str` (moved verbatim from `rpi5/.../assistant/radio.py`).

- [ ] **Step 1: Write the failing test**

```python
# domains/audiobook-catalog/tests/test_text_norm.py
from audiobook_catalog.text_norm import _fold, _stem_phrase

def test_fold_strips_polish_diacritics():
    assert _fold("Trójkę") == "trojke"

def test_stem_phrase_pads_and_stems():
    assert _stem_phrase("Trójka") == " trojk "
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd domains/audiobook-catalog && python -m pytest tests/test_text_norm.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'audiobook_catalog'`.

- [ ] **Step 3: Write minimal implementation**

```toml
# domains/audiobook-catalog/pyproject.toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "audiobook-catalog"
version = "0.0.1"
description = "Shared audiobook catalog model, resolver, and progress store (domains common lib)."
requires-python = ">=3.11"
dependencies = []

[tool.setuptools.packages.find]
where = ["src"]
```

```python
# domains/audiobook-catalog/src/audiobook_catalog/__init__.py
"""Shared audiobook catalog domain lib — model, resolver, progress store."""
```

```python
# domains/audiobook-catalog/src/audiobook_catalog/text_norm.py
"""Polish accent-fold + crude stemming for spoken-title matching.

Moved verbatim from rpi5 assistant/radio.py so the resolver here and radio
there share one source of truth (domains for common code). Pure stdlib.
"""
from __future__ import annotations

import re
import unicodedata

_VOWELS = frozenset("aeiouy")


def _fold(text: str) -> str:
    """Lower-case and strip diacritics ('Trójkę' → 'trojke')."""
    nfkd = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _stem_token(tok: str) -> str:
    """Drop one trailing vowel so Polish case endings collapse."""
    return tok[:-1] if len(tok) > 3 and tok[-1] in _VOWELS else tok


def _stem_phrase(text: str) -> str:
    """Fold, tokenise and stem; space-padded for whole-token containment tests."""
    toks = re.findall(r"[a-z0-9]+", _fold(text))
    return " " + " ".join(_stem_token(t) for t in toks) + " "
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd domains/audiobook-catalog && python -m pytest tests/test_text_norm.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add domains/audiobook-catalog/pyproject.toml domains/audiobook-catalog/src domains/audiobook-catalog/tests/test_text_norm.py
git commit -m "feat(domains): audiobook-catalog pkg + Polish text_norm helpers"
```

## Task A2: Move `AudiobookDirectory` + `Book` into the shared lib

**Files:**
- Create: `domains/audiobook-catalog/src/audiobook_catalog/directory.py`
- Test: `domains/audiobook-catalog/tests/test_directory.py`

**Interfaces:**
- Consumes: `text_norm._fold`, `text_norm._stem_phrase`.
- Produces: `Book(title, author, chapters:tuple[str,...], slug="")`; `AudiobookDirectory(catalog_path=None)` with `.books`, `.available`, `.by_slug(slug)`, `.resolve(query)->Book|None`, `.offer(limit=6)->list[Book]`. Reads `BLAZEN_AUDIOBOOKS_CATALOG` env or an explicit path.

- [ ] **Step 1: Write the failing test**

```python
# domains/audiobook-catalog/tests/test_directory.py
import json
from audiobook_catalog.directory import AudiobookDirectory

def _catalog(tmp_path):
    p = tmp_path / "catalog.json"
    p.write_text(json.dumps({"version": 1, "books": [
        {"title": "Pan Tadeusz", "author": "Adam Mickiewicz", "slug": "calibre-7",
         "chapters": ["/x/01.mp3", "/x/02.mp3"]},
    ]}, ensure_ascii=False), encoding="utf-8")
    return p

def test_resolve_by_folded_title(tmp_path):
    d = AudiobookDirectory(catalog_path=str(_catalog(tmp_path)))
    assert d.resolve("pan tadeusza").slug == "calibre-7"

def test_by_slug_and_available(tmp_path):
    d = AudiobookDirectory(catalog_path=str(_catalog(tmp_path)))
    assert d.available
    assert d.by_slug("calibre-7").title == "Pan Tadeusz"

def test_missing_catalog_is_empty(tmp_path):
    d = AudiobookDirectory(catalog_path=str(tmp_path / "nope.json"))
    assert not d.available
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd domains/audiobook-catalog && python -m pytest tests/test_directory.py -v`
Expected: FAIL — `ModuleNotFoundError: audiobook_catalog.directory`.

- [ ] **Step 3: Write minimal implementation**

Copy the body of `rpi5/.../assistant/audiobooks.py` into `directory.py`, changing only the imports so the fold/stem helpers come from the shared `text_norm` module:

```python
# domains/audiobook-catalog/src/audiobook_catalog/directory.py
"""Offline audiobook directory — resolve a spoken request to a Polish audiobook.

Moved from rpi5 assistant/audiobooks.py into the shared domains lib. Pure
resolve; a player plays the chapters. Accent-folded/stemmed so Polish endings
and diacritics don't matter.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from audiobook_catalog.text_norm import _fold, _stem_phrase

_DEFAULT = "/var/lib/blazen/audiobooks/catalog.json"


def _tokens(text: str) -> set[str]:
    return {t for t in _stem_phrase(text).split() if t}


@dataclass
class Book:
    title: str
    author: str
    chapters: tuple[str, ...]
    slug: str = ""
    _title: set[str] = field(default_factory=set, repr=False)
    _author: set[str] = field(default_factory=set, repr=False)


class AudiobookDirectory:
    def __init__(self, *, catalog_path: str | None = None) -> None:
        self.books: list[Book] = []
        path = Path(catalog_path or os.environ.get("BLAZEN_AUDIOBOOKS_CATALOG", _DEFAULT))
        try:
            raw = json.loads(path.read_text(encoding="utf-8")).get("books", []) or []
        except (OSError, ValueError):
            raw = []
        for b in raw:
            chapters = tuple(str(c) for c in b.get("chapters", []) if c)
            if not chapters:
                continue
            title, author = str(b.get("title", "")), str(b.get("author", ""))
            slug = str(b.get("slug", "")) or _fold(title).replace(" ", "-")
            self.books.append(Book(title=title, author=author, chapters=chapters, slug=slug,
                                   _title=_tokens(title), _author=_tokens(author)))

    def by_slug(self, slug: str) -> Book | None:
        return next((b for b in self.books if b.slug == slug), None)

    @property
    def available(self) -> bool:
        return bool(self.books)

    def resolve(self, query: str) -> Book | None:
        q = _tokens(query)
        if not q:
            return None
        best_score = 0
        best: Book | None = None
        for b in self.books:
            if q <= b._title and b._title:
                score = 100 + len(q & b._title)
            elif q <= b._author and b._author:
                score = 60
            else:
                score = len(q & (b._title | b._author))
            if score > best_score:
                best_score, best = score, b
        return best if best_score > 0 else None

    def offer(self, limit: int = 6) -> list[Book]:
        return self.books[:limit]


__all__ = ["AudiobookDirectory", "Book"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd domains/audiobook-catalog && python -m pytest tests/test_directory.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add domains/audiobook-catalog/src/audiobook_catalog/directory.py domains/audiobook-catalog/tests/test_directory.py
git commit -m "feat(domains): move AudiobookDirectory into audiobook-catalog"
```

## Task A3: Move `AudiobookProgress` into the shared lib

**Files:**
- Create: `domains/audiobook-catalog/src/audiobook_catalog/progress.py`
- Test: `domains/audiobook-catalog/tests/test_progress.py`

**Interfaces:**
- Produces: `AudiobookProgress(path=None)` with `.get(slug)`, `.save(slug, *, chapter, offset_s, title="", updated="")`, `.clear(slug)`. Reads `BLAZEN_AUDIOBOOK_PROGRESS` env or explicit path. Atomic write.

- [ ] **Step 1: Write the failing test**

```python
# domains/audiobook-catalog/tests/test_progress.py
from audiobook_catalog.progress import AudiobookProgress

def test_save_get_clear_roundtrip(tmp_path):
    p = tmp_path / "progress.json"
    a = AudiobookProgress(path=str(p))
    a.save("calibre-7", chapter=2, offset_s=13.5, title="Pan Tadeusz", updated="2026-07-06T10:00")
    b = AudiobookProgress(path=str(p))  # reload from disk
    got = b.get("calibre-7")
    assert got == {"chapter": 2, "offset_s": 13.5, "title": "Pan Tadeusz", "updated": "2026-07-06T10:00"}
    b.clear("calibre-7")
    assert AudiobookProgress(path=str(p)).get("calibre-7") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd domains/audiobook-catalog && python -m pytest tests/test_progress.py -v`
Expected: FAIL — `ModuleNotFoundError: audiobook_catalog.progress`.

- [ ] **Step 3: Write minimal implementation**

Copy `rpi5/.../assistant/audiobook_progress.py` verbatim into `progress.py` (no import changes needed — it's stdlib-only).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd domains/audiobook-catalog && python -m pytest tests/test_progress.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add domains/audiobook-catalog/src/audiobook_catalog/progress.py domains/audiobook-catalog/tests/test_progress.py
git commit -m "feat(domains): move AudiobookProgress into audiobook-catalog"
```

## Task A4: Repoint rpi5 to the shared lib (Pi stays byte-for-byte the same)

**Files:**
- Modify: `rpi5/.../assistant/audiobooks.py` (replace body with re-export shim)
- Modify: `rpi5/.../assistant/audiobook_progress.py` (replace body with re-export shim)
- Modify: `rpi5/.../assistant/radio.py:21-38` (import fold/stem from the shared lib instead of defining them)
- Modify: `rpi5/pyproject.toml` (or the appliance's dep manifest) to add a path dependency on `domains/audiobook-catalog`

**Interfaces:**
- Consumes: everything produced in A1–A3.
- Produces: unchanged public symbols `AudiobookDirectory`, `Book`, `AudiobookProgress`, `_fold`, `_stem_token`, `_stem_phrase` at their existing import paths.

- [ ] **Step 1: Add the path dependency**

In `rpi5/pyproject.toml` dependencies add: `"audiobook-catalog"` and, under `[tool.uv.sources]` (or the equivalent editable/path mechanism the repo uses), point it at `{ path = "../domains/audiobook-catalog", editable = true }`. Then `cd rpi5 && pip install -e ../domains/audiobook-catalog`.

- [ ] **Step 2: Run the existing rpi5 audiobook tests to confirm current green baseline**

Run: `cd rpi5 && python -m pytest tests -k "audiobook" -v`
Expected: PASS (record the count — this must not change).

- [ ] **Step 3: Replace the moved modules with shims**

```python
# rpi5/.../assistant/audiobooks.py  (whole file)
"""Re-export shim — implementation moved to the shared domains lib.
See domains/audiobook-catalog. Kept so existing imports are unchanged."""
from audiobook_catalog.directory import AudiobookDirectory, Book  # noqa: F401

__all__ = ["AudiobookDirectory", "Book"]
```

```python
# rpi5/.../assistant/audiobook_progress.py  (whole file)
"""Re-export shim — implementation moved to the shared domains lib.
See domains/audiobook-catalog."""
from audiobook_catalog.progress import AudiobookProgress  # noqa: F401

__all__ = ["AudiobookProgress"]
```

In `radio.py`, delete the local `_fold`/`_stem_token`/`_stem_phrase` defs and `_VOWELS`, and add at the top:

```python
from audiobook_catalog.text_norm import _VOWELS, _fold, _stem_phrase, _stem_token  # noqa: F401
```
(Export `_VOWELS` from `text_norm.py` too if `radio.py` references it elsewhere — grep first; if unused, drop it from the import.)

- [ ] **Step 4: Run rpi5 audiobook + radio tests to verify unchanged behavior**

Run: `cd rpi5 && python -m pytest tests -k "audiobook or radio" -v`
Expected: PASS with the same count as Step 2.

- [ ] **Step 5: Run the fast gate**

Run: `make test-fast`
Expected: PASS (lint + Tier 0/1). If lint flags unused imports, remove them.

- [ ] **Step 6: Commit**

```bash
git add rpi5
git commit -m "refactor(rpi5): import audiobook engine from domains/audiobook-catalog"
```

## Task A5: `macos/agent/` scaffold + Calibre metadata reader

**Files:**
- Create: `macos/agent/pyproject.toml`
- Create: `macos/agent/src/rachel/__init__.py`
- Create: `macos/agent/src/rachel/calibre.py`
- Create: `macos/agent/.gitignore` (`*.aiff`, `*.mp3`, `.secrets.env`, `.venv/`)
- Test: `macos/agent/tests/test_calibre.py`
- Test fixture: `macos/agent/tests/fixtures/metadata_min.db` (built in Step 1)

**Interfaces:**
- Produces: `calibre.CalibreLibrary(db_path, library_root)` with `.polish_books()->list[CalibreBook]` and `.resolve(query)->CalibreBook|None`; `CalibreBook(id:int, title:str, author:str, language:str, path:str, formats:list[str])` and `.slug` property returning `f"calibre-{id}"`.

- [ ] **Step 1: Write the failing test (builds a tiny sqlite fixture mirroring Calibre's schema)**

```python
# macos/agent/tests/test_calibre.py
import sqlite3
import pytest
from rachel.calibre import CalibreLibrary

@pytest.fixture
def lib(tmp_path):
    db = tmp_path / "metadata.db"
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE books(id INTEGER PRIMARY KEY, title TEXT, path TEXT);
        CREATE TABLE authors(id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE books_authors_link(book INTEGER, author INTEGER);
        CREATE TABLE languages(id INTEGER PRIMARY KEY, lang_code TEXT);
        CREATE TABLE books_languages_link(book INTEGER, lang_code INTEGER);
        CREATE TABLE data(book INTEGER, format TEXT, name TEXT);
        INSERT INTO books VALUES (7, 'Pan Tadeusz', 'Adam Mickiewicz/Pan Tadeusz (7)');
        INSERT INTO authors VALUES (1, 'Adam Mickiewicz');
        INSERT INTO books_authors_link VALUES (7, 1);
        INSERT INTO languages VALUES (1, 'pol'), (2, 'eng');
        INSERT INTO books_languages_link VALUES (7, 1);
        INSERT INTO data VALUES (7, 'EPUB', 'Pan Tadeusz - Adam Mickiewicz');
        INSERT INTO books VALUES (9, 'Some English Book', 'X/Y (9)');
        INSERT INTO books_languages_link VALUES (9, 2);
        """
    )
    con.commit(); con.close()
    (tmp_path / "Adam Mickiewicz" / "Pan Tadeusz (7)").mkdir(parents=True)
    return CalibreLibrary(db_path=str(db), library_root=str(tmp_path))

def test_polish_books_filters_language(lib):
    books = lib.polish_books()
    assert [b.id for b in books] == [7]
    assert books[0].author == "Adam Mickiewicz"
    assert books[0].slug == "calibre-7"
    assert "EPUB" in books[0].formats

def test_resolve_by_title(lib):
    assert lib.resolve("pan tadeusz").id == 7
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd macos/agent && python -m pytest tests/test_calibre.py -v`
Expected: FAIL — `ModuleNotFoundError: rachel`.

- [ ] **Step 3: Write minimal implementation**

```toml
# macos/agent/pyproject.toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "rachel"
version = "0.0.1"
description = "rachel (macOS) audiobook agent — Calibre ingest + Apple/Azure TTS."
requires-python = ">=3.11"
dependencies = ["audiobook-catalog", "ebooklib>=0.18", "beautifulsoup4>=4.12"]

[project.optional-dependencies]
azure = ["azure-cognitiveservices-speech>=1.38"]

[project.scripts]
rachel-audiobook = "rachel.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.uv.sources]
audiobook-catalog = { path = "../../domains/audiobook-catalog", editable = true }
```

```python
# macos/agent/src/rachel/calibre.py
"""Read a Calibre library's metadata.db and locate Polish ebooks + their files."""
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
    path: str          # book dir relative to library root
    formats: list[str]
    _title: set[str] = field(default_factory=set, repr=False)

    @property
    def slug(self) -> str:
        return f"calibre-{self.id}"

    def format_file(self, library_root: str, fmt: str) -> Path | None:
        """Absolute path to the on-disk file for a given format (EPUB/MOBI/...)."""
        d = Path(library_root) / self.path
        for f in d.glob(f"*.{fmt.lower()}"):
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
        q = _tokens(query)
        if not q:
            return None
        best, best_score = None, 0
        for b in self.polish_books():
            score = (100 if q <= b._title else 0) + len(q & b._title)
            if score > best_score:
                best, best_score = b, score
        return best if best_score else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd macos/agent && pip install -e ../../domains/audiobook-catalog && pip install -e . && python -m pytest tests/test_calibre.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add macos/agent/pyproject.toml macos/agent/src macos/agent/tests macos/agent/.gitignore
git commit -m "feat(macos): rachel agent scaffold + Calibre metadata reader"
```

## Task A6: EPUB → chapter text extraction

**Files:**
- Create: `macos/agent/src/rachel/extract.py`
- Test: `macos/agent/tests/test_extract.py`

**Interfaces:**
- Produces: `extract.epub_to_chapters(epub_path)->list[Chapter]` and `extract.text_to_chapters(txt, *, max_chars=6000)->list[Chapter]`; `Chapter(index:int, title:str, text:str)`. EPUB path uses `ebooklib` spine → one `Chapter` per spine document (HTML stripped via BeautifulSoup). `text_to_chapters` is the `ebook-convert` fallback splitter (split on blank-line-delimited headings, then hard-wrap over-long chapters at `max_chars` on paragraph boundaries).

- [ ] **Step 1: Write the failing test**

```python
# macos/agent/tests/test_extract.py
from rachel.extract import text_to_chapters, Chapter

def test_text_split_on_headings():
    txt = "ROZDZIAŁ I\n\nAla ma kota.\n\nROZDZIAŁ II\n\nKot ma Alę.\n"
    chs = text_to_chapters(txt)
    assert [c.index for c in chs] == [0, 1]
    assert "Ala ma kota" in chs[0].text
    assert chs[1].title.startswith("ROZDZIAŁ II")

def test_long_chapter_is_wrapped():
    para = ("Zdanie. " * 400).strip()          # ~3200 chars
    txt = f"ROZDZIAŁ I\n\n{para}\n\n{para}\n"    # ~6400 chars in one heading
    chs = text_to_chapters(txt, max_chars=4000)
    assert len(chs) >= 2
    assert all(len(c.text) <= 4000 for c in chs)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd macos/agent && python -m pytest tests/test_extract.py -v`
Expected: FAIL — `ModuleNotFoundError: rachel.extract`.

- [ ] **Step 3: Write minimal implementation**

```python
# macos/agent/src/rachel/extract.py
"""Extract chapter texts from an ebook (EPUB spine, or a converted .txt)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_HEADING = re.compile(r"^(ROZDZIA[ŁL]|CZ[ĘE][ŚS][ĆC]|CHAPTER|PART)\b.*$", re.IGNORECASE | re.MULTILINE)


@dataclass
class Chapter:
    index: int
    title: str
    text: str


def _wrap(title: str, body: str, max_chars: int, start: int) -> list[Chapter]:
    paras = [p for p in re.split(r"\n\s*\n", body) if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paras:
        if buf and len(buf) + len(p) + 2 > max_chars:
            chunks.append(buf); buf = ""
        buf = f"{buf}\n\n{p}" if buf else p
    if buf:
        chunks.append(buf)
    return [Chapter(index=start + i, title=title if i == 0 else f"{title} ({i+1})",
                    text=c.strip()) for i, c in enumerate(chunks or [""])]


def text_to_chapters(txt: str, *, max_chars: int = 6000) -> list[Chapter]:
    marks = list(_HEADING.finditer(txt))
    out: list[Chapter] = []
    if not marks:
        return _wrap("", txt.strip(), max_chars, 0)
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
        title = (soup.find(["h1", "h2", "h3"]).get_text().strip()
                 if soup.find(["h1", "h2", "h3"]) else Path(item.get_name()).stem)
        for ch in _wrap(title, text, max_chars, len(out)):
            out.append(ch)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd macos/agent && python -m pytest tests/test_extract.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add macos/agent/src/rachel/extract.py macos/agent/tests/test_extract.py
git commit -m "feat(macos): EPUB/text -> chapter extraction"
```

## Task A7: TTS backends (Apple default, Azure premium opt-in)

**Files:**
- Create: `macos/agent/src/rachel/tts.py`
- Test: `macos/agent/tests/test_tts.py`

**Interfaces:**
- Produces: `TtsBackend` protocol with `render_chapter(text:str, out_mp3:Path)->None`; `AppleTTS(voice="Zosia")` and `AzureTTS(voice="pl-PL-MarekNeural", key=...)`. Module fn `apple_commands(text_path, aiff_path, mp3_path, voice)->list[list[str]]` returning the exact `say` + `ffmpeg` argv lists (pure, unit-testable without invoking audio). Module fn `installed_polish_voice_is_compact()->bool` for the quality warning.

- [ ] **Step 1: Write the failing test**

```python
# macos/agent/tests/test_tts.py
from pathlib import Path
from rachel.tts import apple_commands

def test_apple_commands_shape():
    say, conv = apple_commands(Path("/t/ch.txt"), Path("/t/ch.aiff"), Path("/t/07.mp3"), "Zosia")
    assert say[:5] == ["say", "-v", "Zosia", "-f", "/t/ch.txt"]
    assert "-o" in say and "/t/ch.aiff" in say
    assert conv[0] == "ffmpeg" and conv[-1] == "/t/07.mp3"
    assert "/t/ch.aiff" in conv
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd macos/agent && python -m pytest tests/test_tts.py -v`
Expected: FAIL — `ModuleNotFoundError: rachel.tts`.

- [ ] **Step 3: Write minimal implementation**

```python
# macos/agent/src/rachel/tts.py
"""TTS backends: Apple `say` (on-device, default) and Azure Neural (premium opt-in)."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Protocol


class TtsBackend(Protocol):
    def render_chapter(self, text: str, out_mp3: Path) -> None: ...


def apple_commands(text_path: Path, aiff_path: Path, mp3_path: Path, voice: str) -> list[list[str]]:
    """Pure: the two argv lists that render `text_path` → mp3 via `say` then ffmpeg."""
    say = ["say", "-v", voice, "-f", str(text_path), "-o", str(aiff_path)]
    conv = ["ffmpeg", "-y", "-i", str(aiff_path), "-codec:a", "libmp3lame",
            "-qscale:a", "4", str(mp3_path)]
    return [say, conv]


def installed_polish_voice_is_compact(voice: str = "Zosia") -> bool:
    """True when only the compact `voice` is present (no Premium/Enhanced variant)."""
    out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout
    lines = [ln for ln in out.splitlines() if voice.lower() in ln.lower()]
    return not any(("premium" in ln.lower() or "enhanced" in ln.lower()) for ln in lines)


class AppleTTS:
    def __init__(self, voice: str = "Zosia") -> None:
        self.voice = voice

    def render_chapter(self, text: str, out_mp3: Path) -> None:
        out_mp3.parent.mkdir(parents=True, exist_ok=True)
        txt = out_mp3.with_suffix(".txt")
        aiff = out_mp3.with_suffix(".aiff")
        txt.write_text(text, encoding="utf-8")
        try:
            say, conv = apple_commands(txt, aiff, out_mp3, self.voice)
            subprocess.run(say, check=True)
            subprocess.run(conv, check=True, capture_output=True)
        finally:
            for p in (txt, aiff):
                p.unlink(missing_ok=True)


class AzureTTS:
    def __init__(self, *, key: str, region: str = "westeurope",
                 voice: str = "pl-PL-MarekNeural") -> None:
        self.key, self.region, self.voice = key, region, voice

    def render_chapter(self, text: str, out_mp3: Path) -> None:
        import azure.cognitiveservices.speech as sdk  # noqa: PLC0415
        out_mp3.parent.mkdir(parents=True, exist_ok=True)
        cfg = sdk.SpeechConfig(subscription=self.key, region=self.region)
        cfg.speech_synthesis_voice_name = self.voice
        cfg.set_speech_synthesis_output_format(
            sdk.SpeechSynthesisOutputFormat.Audio24Khz96KBitRateMonoMp3)
        out = sdk.audio.AudioOutputConfig(filename=str(out_mp3))
        sdk.SpeechSynthesizer(speech_config=cfg, audio_config=out).speak_text_async(text).get()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd macos/agent && python -m pytest tests/test_tts.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add macos/agent/src/rachel/tts.py macos/agent/tests/test_tts.py
git commit -m "feat(macos): Apple + Azure TTS backends"
```

## Task A8: Ingest orchestration (extract → render → catalog write)

**Files:**
- Create: `macos/agent/src/rachel/paths.py`
- Create: `macos/agent/src/rachel/ingest.py`
- Test: `macos/agent/tests/test_ingest.py`

**Interfaces:**
- Consumes: `CalibreLibrary`/`CalibreBook`, `extract.*`, `TtsBackend`, `AudiobookProgress` (for reads later).
- Produces: `paths.audiobook_root()->Path`, `paths.catalog_path()->Path`; `ingest.upsert_catalog_entry(catalog_path, entry:dict)->None` (load-or-create, replace by slug, atomic write, `version:1`); `ingest.render_book(book, chapters, tts, *, root, premium=False, voice="Zosia", on_chapter=None)->dict` returning the catalog entry it wrote. Renders progressively, calling `on_chapter(index, mp3_path)` after each chapter so a caller can start playback.

- [ ] **Step 1: Write the failing test (fake TTS — writes a stub file, no audio)**

```python
# macos/agent/tests/test_ingest.py
import json
from pathlib import Path
from rachel.calibre import CalibreBook
from rachel.extract import Chapter
from rachel.ingest import render_book, upsert_catalog_entry

class FakeTTS:
    def render_chapter(self, text, out_mp3: Path):
        out_mp3.parent.mkdir(parents=True, exist_ok=True)
        out_mp3.write_bytes(b"ID3stub")

def test_render_book_writes_chapters_and_entry(tmp_path):
    book = CalibreBook(id=7, title="Pan Tadeusz", author="Adam Mickiewicz",
                       language="pol", path="x", formats=["EPUB"])
    chapters = [Chapter(0, "R I", "Ala."), Chapter(1, "R II", "Kot.")]
    seen = []
    entry = render_book(book, chapters, FakeTTS(), root=tmp_path,
                        on_chapter=lambda i, p: seen.append(i))
    assert entry["slug"] == "calibre-7"
    assert entry["n_chapters"] == 2
    assert entry["source"] == "calibre-tts" and entry["language"] == "pl"
    assert seen == [0, 1]
    assert (tmp_path / "calibre-7" / "00.mp3").exists()
    assert (tmp_path / "calibre-7" / "01.mp3").read_bytes() == b"ID3stub"

def test_upsert_replaces_by_slug(tmp_path):
    cat = tmp_path / "catalog.json"
    upsert_catalog_entry(cat, {"slug": "calibre-7", "title": "old", "chapters": ["a"]})
    upsert_catalog_entry(cat, {"slug": "calibre-7", "title": "new", "chapters": ["a"]})
    upsert_catalog_entry(cat, {"slug": "calibre-9", "title": "other", "chapters": ["b"]})
    data = json.loads(cat.read_text())
    assert data["version"] == 1
    titles = {b["slug"]: b["title"] for b in data["books"]}
    assert titles == {"calibre-7": "new", "calibre-9": "other"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd macos/agent && python -m pytest tests/test_ingest.py -v`
Expected: FAIL — `ModuleNotFoundError: rachel.ingest`.

- [ ] **Step 3: Write minimal implementation**

```python
# macos/agent/src/rachel/paths.py
"""Mac-local blazen paths."""
from __future__ import annotations

import os
from pathlib import Path

def audiobook_root() -> Path:
    env = os.environ.get("BLAZEN_AUDIOBOOKS_ROOT")
    base = Path(env) if env else Path.home() / "Library/Application Support/blazen/audiobooks"
    base.mkdir(parents=True, exist_ok=True)
    return base

def catalog_path() -> Path:
    return audiobook_root() / "catalog.json"

def progress_path() -> Path:
    return audiobook_root() / "progress.json"
```

```python
# macos/agent/src/rachel/ingest.py
"""Orchestrate extract → progressive TTS render → shared catalog entry."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from rachel.calibre import CalibreBook
from rachel.extract import Chapter
from rachel.tts import TtsBackend


def upsert_catalog_entry(catalog_path: Path, entry: dict) -> None:
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {"version": 1, "books": []}
    books = [b for b in data.get("books", []) if b.get("slug") != entry.get("slug")]
    books.append(entry)
    data = {"version": 1, "books": books}
    tmp = catalog_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(catalog_path)


def render_book(book: CalibreBook, chapters: list[Chapter], tts: TtsBackend, *,
                root: Path, premium: bool = False, voice: str = "Zosia",
                on_chapter: Callable[[int, Path], None] | None = None) -> dict:
    slug = book.slug
    book_dir = Path(root) / slug
    paths: list[str] = []
    for ch in chapters:
        mp3 = book_dir / f"{ch.index:02d}.mp3"
        tts.render_chapter(ch.text, mp3)
        paths.append(str(mp3))
        if on_chapter:
            on_chapter(ch.index, mp3)
    return {
        "author": book.author, "title": book.title, "slug": slug,
        "genre": "", "epoch": "", "downloaded": True,
        "chapters": paths, "n_chapters": len(paths),
        "source": "calibre-tts", "language": "pl",
        "voice": voice, "premium": premium,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd macos/agent && python -m pytest tests/test_ingest.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add macos/agent/src/rachel/paths.py macos/agent/src/rachel/ingest.py macos/agent/tests/test_ingest.py
git commit -m "feat(macos): progressive render + catalog upsert"
```

## Task A9: CLI (`rachel-audiobook`) + secrets loader + quality warning

**Files:**
- Create: `macos/agent/src/rachel/secrets.py`
- Create: `macos/agent/src/rachel/cli.py`
- Create: `macos/Makefile`
- Create: `macos/.secrets.env.example`
- Test: `macos/agent/tests/test_cli.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `secrets.load(path=None)->dict[str,str]` (parses `KEY=VALUE` lines from `macos/.secrets.env`, ignoring `#`); `cli.main(argv=None)->int` implementing `list`, `render <query|--id N> [--premium] [--voice V]`, `play <query>`, `resume`. `render` picks EPUB→`epub_to_chapters` else `ebook-convert`→`text_to_chapters`, warns if `installed_polish_voice_is_compact()`, calls `render_book`, and (unless `--no-play`) invokes the player from Task B4.

- [ ] **Step 1: Write the failing test**

```python
# macos/agent/tests/test_cli.py
from rachel.secrets import load

def test_secrets_parse(tmp_path):
    f = tmp_path / ".secrets.env"
    f.write_text("# comment\nAZURE_SPEECH_KEY=abc123\nAZURE_REGION=westeurope\n")
    d = load(str(f))
    assert d["AZURE_SPEECH_KEY"] == "abc123"
    assert d["AZURE_REGION"] == "westeurope"
    assert "comment" not in d

def test_secrets_missing_file_is_empty(tmp_path):
    assert load(str(tmp_path / "nope.env")) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd macos/agent && python -m pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: rachel.secrets`.

- [ ] **Step 3: Write minimal implementation**

```python
# macos/agent/src/rachel/secrets.py
"""Parse macos/.secrets.env (gitignored). Never logs values."""
from __future__ import annotations

from pathlib import Path

def load(path: str | None = None) -> dict[str, str]:
    p = Path(path) if path else Path(__file__).resolve().parents[3] / ".secrets.env"
    out: dict[str, str] = {}
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    except OSError:
        return {}
    return out
```

```python
# macos/agent/src/rachel/cli.py
"""rachel-audiobook — render Calibre ebooks to Polish audiobooks and play them."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from rachel import paths, secrets
from rachel.calibre import CalibreLibrary
from rachel.extract import epub_to_chapters, text_to_chapters
from rachel.ingest import render_book, upsert_catalog_entry
from rachel.tts import AppleTTS, AzureTTS, installed_polish_voice_is_compact

_CALIBRE = os.environ.get("CALIBRE_LIBRARY", str(Path.home() / "calibre"))


def _library() -> CalibreLibrary:
    return CalibreLibrary(db_path=str(Path(_CALIBRE) / "metadata.db"), library_root=_CALIBRE)


def _chapters_for(book, lib):
    epub = book.format_file(lib.library_root, "EPUB")
    if epub:
        return epub_to_chapters(str(epub))
    src = next((book.format_file(lib.library_root, f) for f in book.formats
                if book.format_file(lib.library_root, f)), None)
    if not src:
        raise SystemExit(f"no readable format for {book.slug}")
    txt = Path(src).with_suffix(".rachel.txt")
    subprocess.run(["ebook-convert", str(src), str(txt)], check=True, capture_output=True)
    chs = text_to_chapters(txt.read_text(encoding="utf-8", errors="ignore"))
    txt.unlink(missing_ok=True)
    return chs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="rachel-audiobook")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    r = sub.add_parser("render"); r.add_argument("query", nargs="*"); r.add_argument("--id", type=int)
    r.add_argument("--premium", action="store_true"); r.add_argument("--voice", default="Zosia")
    r.add_argument("--no-play", action="store_true")
    p = sub.add_parser("play"); p.add_argument("query", nargs="+")
    sub.add_parser("resume")
    a = ap.parse_args(argv)
    lib = _library()

    if a.cmd == "list":
        for b in lib.polish_books():
            print(f"{b.slug:14} {b.author} — {b.title}  [{','.join(b.formats)}]")
        return 0

    if a.cmd == "render":
        book = next((x for x in lib.polish_books() if x.id == a.id), None) if a.id \
            else lib.resolve(" ".join(a.query))
        if not book:
            print("book not found", file=sys.stderr); return 1
        if a.premium:
            s = secrets.load()
            key = s.get("AZURE_SPEECH_KEY")
            if not key:
                print("--premium needs AZURE_SPEECH_KEY in macos/.secrets.env", file=sys.stderr); return 1
            tts = AzureTTS(key=key, region=s.get("AZURE_REGION", "westeurope"))
        else:
            if installed_polish_voice_is_compact(a.voice):
                print(f"note: only the compact '{a.voice}' voice is installed — "
                      "for audiobook quality download the Premium/Enhanced voice in "
                      "System Settings → Accessibility → Spoken Content → System Voices.",
                      file=sys.stderr)
            tts = AppleTTS(voice=a.voice)
        chs = _chapters_for(book, lib)
        first = {"done": False}
        def _kick(i: int, mp3: Path) -> None:
            if not first["done"] and not a.no_play:
                first["done"] = True
                subprocess.Popen(["afplay", str(mp3)])   # smoke playback until Phase B player
        entry = render_book(book, chs, tts, root=paths.audiobook_root(),
                            premium=a.premium, voice=a.voice, on_chapter=_kick)
        upsert_catalog_entry(paths.catalog_path(), entry)
        print(f"rendered {entry['slug']}: {entry['n_chapters']} ch → {paths.catalog_path()}")
        return 0

    if a.cmd in ("play", "resume"):
        print("play/resume land in Phase B (rachel-player).", file=sys.stderr)
        return 2
    return 2
```

```make
# macos/Makefile
AGENT := agent
.PHONY: venv test lint render play
venv:
	cd $(AGENT) && python3 -m venv .venv && . .venv/bin/activate && \
	  pip install -e ../../domains/audiobook-catalog && pip install -e '.[azure]' && pip install pytest ruff
test:
	cd $(AGENT) && python -m pytest -q
lint:
	cd $(AGENT) && ruff check src tests
render:
	cd $(AGENT) && python -m rachel.cli render $(BOOK)
```

```bash
# macos/.secrets.env.example  (copy to macos/.secrets.env — gitignored)
AZURE_SPEECH_KEY=
AZURE_REGION=westeurope
```

- [ ] **Step 4: Run test + confirm `.secrets.env` is ignored**

Run: `cd macos/agent && python -m pytest tests/test_cli.py -v` → Expected: PASS (2 passed).
Run: `git check-ignore macos/.secrets.env` → Expected: prints the path (ignored). If not, add `.secrets.env` to `macos/agent/.gitignore` and a `macos/.gitignore`.

- [ ] **Step 5: Full Phase A suite + manual smoke**

Run: `cd macos/agent && python -m pytest -q` → Expected: all pass.
Manual (not committed): `python -m rachel.cli list | head` then `python -m rachel.cli render "<a short Polish title>"` → hear Zosia via `afplay`, and a catalog entry appears.

- [ ] **Step 6: Commit**

```bash
git add macos/agent/src/rachel/secrets.py macos/agent/src/rachel/cli.py macos/Makefile macos/.secrets.env.example macos/agent/.gitignore
git commit -m "feat(macos): rachel-audiobook CLI (list/render) + secrets + quality warning"
```

---

# PHASE B — Rust: portable player engine in domains + CoreAudio Mac player

> **Coordination:** `rpi5/voice-output/blazend-player` is under **active development** by the jessica session (it just gained a leveler + speech compressor + limiter dynamics chain, commit `eecc228`). Before starting Phase B, `git pull --rebase` and re-read the current `blazend-player/src/main.rs`. Extract the code **as it is then**, including the dynamics chain, into the shared core. Keep the extraction a pure code-move (no behavior change) so a later diff against the Pi binary is reviewable.

## Task B1: `domains/blazend-audiobook` crate — `AudioSink` trait + decode/seek engine

**Files:**
- Create: `domains/blazend-audiobook/Cargo.toml`
- Create: `domains/blazend-audiobook/src/lib.rs`
- Create: `domains/blazend-audiobook/src/sink.rs` (the `AudioSink` trait)
- Create: `domains/blazend-audiobook/src/engine.rs` (decode + seek + position-file + auto-advance, moved from `blazend-player`)
- Create: `domains/blazend-audiobook/src/dynamics.rs` (leveler + compressor + limiter, moved from `blazend-player`)
- Modify: `domains/Cargo.toml` (add the crate to `[workspace] members`)
- Test: inline `#[cfg(test)]` in `engine.rs` with a mock sink

**Interfaces:**
- Produces:
  - `trait AudioSink { fn write(&mut self, interleaved: &[f32]) -> anyhow::Result<()>; fn sample_rate(&self) -> u32; }`
  - `struct PlayerConfig { chapters: Vec<PathBuf>, start_chapter: usize, start_seconds: f64, position_file: Option<PathBuf>, dynamics: DynamicsConfig }`
  - `fn play<S: AudioSink>(sink: &mut S, cfg: PlayerConfig) -> anyhow::Result<()>` — plays each chapter from `start_chapter`, seeking `start_seconds` into the first, auto-advancing, writing `{chapter, offset_s}` to `position_file` ~every second.
  - `struct DynamicsConfig { level: bool, target_db: f32, max_boost_db: f32, compress: bool, comp_threshold_db: f32, comp_ratio: f32, comp_makeup_db: f32, limit_db: f32 }` + `struct Dynamics` with `fn process(&mut self, buf: &mut [f32])` (moved from the Pi's `Leveler`/dynamics struct).

- [ ] **Step 1: Write the failing test (mock sink; a generated sine WAV fixture)**

```rust
// in engine.rs
#[cfg(test)]
mod tests {
    use super::*;
    struct MockSink { frames: usize, sr: u32 }
    impl AudioSink for MockSink {
        fn write(&mut self, b: &[f32]) -> anyhow::Result<()> { self.frames += b.len(); Ok(()) }
        fn sample_rate(&self) -> u32 { self.sr }
    }
    #[test]
    fn plays_all_chapters_and_advances() {
        let dir = tempfile::tempdir().unwrap();
        let chapters = crate::test_util::write_two_short_wavs(dir.path()); // 0.2s each
        let mut sink = MockSink { frames: 0, sr: 48_000 };
        let pos = dir.path().join("pos.json");
        play(&mut sink, PlayerConfig {
            chapters, start_chapter: 0, start_seconds: 0.0,
            position_file: Some(pos.clone()), dynamics: DynamicsConfig::off(),
        }).unwrap();
        assert!(sink.frames > 0);
        assert!(pos.exists()); // final position written
    }
    #[test]
    fn start_seconds_skips_frames() {
        // playing with start_seconds=0.1 into a 0.2s file writes ~half the frames
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd domains && cargo test -p blazend-audiobook`
Expected: FAIL — crate/target not found (not yet a workspace member) or `play` undefined.

- [ ] **Step 3: Write minimal implementation**

Add the crate to `domains/Cargo.toml` `members`. Author `Cargo.toml` with `symphonia` (workspace version), `anyhow`, `serde`/`serde_json`, and `tempfile` as a dev-dependency. Move the decode/seek/auto-advance loop and the dynamics chain out of `blazend-player/src/main.rs` into `engine.rs` + `dynamics.rs`, replacing the direct ALSA writes with `sink.write(&interleaved)`. Add a `test_util` module (behind `#[cfg(test)]`) that writes short WAVs via `hound` (dev-dep). Implement `DynamicsConfig::off()`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd domains && cargo test -p blazend-audiobook`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add domains/blazend-audiobook domains/Cargo.toml domains/Cargo.lock
git commit -m "feat(domains): blazend-audiobook portable engine + AudioSink trait"
```

## Task B2: CoreAudio/cpal sink

**Files:**
- Create: `domains/blazend-audiobook/src/sink_cpal.rs` (feature-gated `cpal` sink, usable by any platform)
- Modify: `domains/blazend-audiobook/Cargo.toml` (add optional `cpal` dep + `cpal-sink` feature)
- Test: inline test that opens a cpal sink in a headless-tolerant way (skips if no default device)

**Interfaces:**
- Produces: `struct CpalSink` implementing `AudioSink`, constructed via `CpalSink::default_output() -> anyhow::Result<CpalSink>`; buffers samples to the cpal output stream at the device sample rate (resample in the engine if needed, or expose `sample_rate()` from the device).

- [ ] **Step 1–4:** TDD as above — a test that constructs `CpalSink::default_output()` and, if a device exists, writes 0.1s of silence without error; `#[ignore]`/skip when `cpal::default_host().default_output_device()` is `None` (CI has no audio). Implement with `cpal` build-output-stream + a ring buffer drained by the audio callback.

- [ ] **Step 5: Commit**

```bash
git add domains/blazend-audiobook/src/sink_cpal.rs domains/blazend-audiobook/Cargo.toml domains/Cargo.lock
git commit -m "feat(domains): cpal AudioSink for blazend-audiobook"
```

## Task B3: `macos/player` — `rachel-player` binary

**Files:**
- Create: `macos/player/Cargo.toml` (bin; deps: `blazend-audiobook` with `cpal-sink`, `clap`, `anyhow`)
- Create: `macos/player/src/main.rs`
- Modify: `domains/Cargo.toml` or a `macos/player` standalone — decide: add `macos/player` to the `domains` workspace members OR give it its own workspace. Prefer its own `[workspace]` in `macos/player/Cargo.toml` with a path dep on `../../domains/blazend-audiobook`, mirroring how `rpi5/crates` depends one-way on `domains/`.

**Interfaces:**
- Produces: a `rachel-player` binary with flags mirroring the Pi player subset: `--chapters <json-or-comma-list>` (or repeated `--chapter`), `--start-chapter N`, `--start-seconds F`, `--position-file P`, `--compress`, `--no-level`. Builds a `CpalSink`, constructs `PlayerConfig`, calls `blazend_audiobook::play`.

- [ ] **Step 1: Write the failing test**

```rust
// macos/player/tests/cli.rs — parses args into PlayerConfig without playing
#[test]
fn parses_chapters_and_start() {
    let cfg = rachel_player::parse(["rachel-player",
        "--chapter", "/a/00.mp3", "--chapter", "/a/01.mp3",
        "--start-chapter", "1", "--start-seconds", "12.5"].map(String::from).to_vec()).unwrap();
    assert_eq!(cfg.chapters.len(), 2);
    assert_eq!(cfg.start_chapter, 1);
    assert!((cfg.start_seconds - 12.5).abs() < 1e-6);
}
```

- [ ] **Step 2–4:** Run `cd macos/player && cargo test` → FAIL (no crate) → implement `parse()` (clap) returning `PlayerConfig` + a `main()` that builds the sink and calls `play` → PASS.

- [ ] **Step 5: Commit**

```bash
git add macos/player
git commit -m "feat(macos): rachel-player binary over blazend-audiobook + cpal"
```

## Task B4: Wire `rachel-audiobook play/resume` to the player + progress

**Files:**
- Modify: `macos/agent/src/rachel/cli.py` (implement `play`/`resume`)
- Create: `macos/agent/src/rachel/player.py` (locate + invoke the `rachel-player` binary)
- Test: `macos/agent/tests/test_player_invoke.py`

**Interfaces:**
- Consumes: `AudiobookDirectory` (over the Mac catalog), `AudiobookProgress`, `paths.*`, the `rachel-player` binary.
- Produces: `player.play_book(book, *, start_chapter=0, start_seconds=0.0, position_file)->list[str]` returning the exact `rachel-player` argv (pure/testable); `cli` `play <query>` resolves via `AudiobookDirectory`, reads any saved progress, and spawns the player; `resume` replays the most-recently-updated slug from `progress.json`.

- [ ] **Step 1: Write the failing test**

```python
# macos/agent/tests/test_player_invoke.py
from rachel.player import play_book_argv

def test_play_argv_includes_chapters_and_resume():
    argv = play_book_argv("/bin/rachel-player",
                          ["/a/00.mp3", "/a/01.mp3"],
                          start_chapter=1, start_seconds=9.0,
                          position_file="/p/progress-pos.json")
    assert argv[0] == "/bin/rachel-player"
    assert "--start-chapter" in argv and "1" in argv
    assert argv.count("--chapter") == 2
    assert "/p/progress-pos.json" in argv
```

- [ ] **Step 2–4:** FAIL (no `rachel.player`) → implement `play_book_argv` + a `_binary()` locator (env `RACHEL_PLAYER_BIN` → `macos/player/target/release/rachel-player`) and the `cli` `play`/`resume` handlers that map `AudiobookProgress` ↔ the player's `--position-file` → PASS.

- [ ] **Step 5: Full suite + manual end-to-end smoke**

Run: `cd macos/agent && python -m pytest -q` → all pass.
Run: `cd macos/player && cargo build --release`.
Manual: `python -m rachel.cli render "<title>" --no-play` then `python -m rachel.cli play "<title>"` → plays on the Mac with auto-advance; Ctrl-C, then `python -m rachel.cli resume` → resumes near the stop point.

- [ ] **Step 6: Commit**

```bash
git add macos/agent/src/rachel/player.py macos/agent/src/rachel/cli.py macos/agent/tests/test_player_invoke.py
git commit -m "feat(macos): rachel-audiobook play/resume via rachel-player + progress"
```

---

# Phase C (separate, Pi-owned plan — not built here)

Rewire `rpi5/voice-output/blazend-player` to link `domains/blazend-audiobook` + an ALSA `AudioSink` (delete the now-duplicated engine/dynamics from `main.rs`), keeping the CLI + `make test-fast` green. Coordinate with the jessica session; write its own plan when Phase B has stabilized the shared engine's API.

---

## Docs to sync after B (per repo maintenance rule)
- `macos/docs/01-ARCHITECTURE.md` — update "rachel does NOT render books" → rachel renders via Apple TTS (domains-first).
- `macos/docs/04-CALIBRE-TTS.md` — Apple default + Azure premium; Mac-local root; `domains/audiobook-catalog` + `domains/blazend-audiobook`.
- `macos/README.md` + `macos/docs/README.md` — add `rachel-audiobook` usage.
- Root `docs/19-DOMAIN-ARCHITECTURE.md` — note `domains/audiobook-catalog` (first Python domain lib) + `domains/blazend-audiobook`.
