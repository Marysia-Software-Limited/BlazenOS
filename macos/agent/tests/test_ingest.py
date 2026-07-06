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
    assert entry["voice"] == "Zosia" and entry["premium"] is False
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


def test_rendered_entry_resolvable_via_shared_directory(tmp_path):
    # The catalog rachel writes must be readable by the shared AudiobookDirectory.
    from audiobook_catalog.directory import AudiobookDirectory
    book = CalibreBook(id=35, title="Metro 2033", author="Glukhovsky",
                       language="pol", path="x", formats=["EPUB"])
    entry = render_book(book, [Chapter(0, "", "tekst")], FakeTTS(), root=tmp_path)
    cat = tmp_path / "catalog.json"
    upsert_catalog_entry(cat, entry)
    d = AudiobookDirectory(catalog_path=str(cat))
    assert d.resolve("metro 2033").slug == "calibre-35"
