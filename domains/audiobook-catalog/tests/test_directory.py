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
    assert d.resolve("cokolwiek") is None
