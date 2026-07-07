"""Unit tests for the book reader's text cleaning + chunking (no ebooklib / TTS)."""
from __future__ import annotations

import json

from mesh_registry import Mesh

from jessica_linux.books import chunks, clean_text, published_catalog, pull_catalog


def test_clean_text_strips_artifacts():
    # soft hyphen inside a word, mid-paragraph line breaks, (...) marks, page number
    dirty = "Ciem­ny tunel\nmetra\n„(...)” cisza.\n\n42\n\nDrugi akapit."
    out = clean_text(dirty)
    assert "Ciemny tunel metra" in out          # soft hyphen removed, line breaks joined
    assert "(...)" not in out and "…" not in out  # editorial marks gone
    assert "\n42\n" not in out and "\n\n42" not in out  # standalone page number dropped
    assert "\n\nDrugi akapit." in out           # paragraph break preserved


def test_clean_text_keeps_numbers_in_titles_and_inline():
    assert clean_text("Metro\n2033 Gluchovsky") == "Metro 2033 Gluchovsky"  # joined, not dropped
    assert "rok 1984" in clean_text("Był\nrok 1984 wtedy.")


def test_chunks_respect_max_and_split_paragraphs():
    chapter = "Akapit pierwszy.\n\n" + ("A" * 500) + "\n\n" + ("B" * 500)
    out = chunks([chapter], max_chars=700)
    assert len(out) >= 2
    assert all(len(c) <= 700 for c in out)
    # content is preserved (order + text), just repackaged
    assert "Akapit pierwszy." in out[0]
    assert any("B" * 100 in c for c in out)


def test_long_paragraph_is_sentence_split():
    giant = ". ".join(f"Zdanie numer {i}" for i in range(200)) + "."
    out = chunks([giant], max_chars=300)
    assert out and all(len(c) <= 300 for c in out)
    # sentence boundaries preferred — most chunks end at a period
    assert sum(c.endswith(".") for c in out) >= len(out) - 1


def test_empty_and_tiny_chapters():
    assert chunks([]) == []
    assert chunks(["krótko"], max_chars=700) == ["krótko"]


# -- catalog publish / merge (no network) -------------------------------------


def test_published_catalog_rewrites_urls_and_filters(tmp_path):
    (tmp_path / "calibre-35").mkdir()
    (tmp_path / "calibre-35" / "001.mp3").write_bytes(b"x")
    cat = {"version": 1, "books": [
        {"slug": "calibre-35", "title": "Metro", "chapters": ["calibre-35/001.mp3"]},
        {"slug": "wl-x", "title": "WL", "chapters": ["wl-x/001.mp3"]},  # no file here
    ]}
    (tmp_path / "catalog.json").write_text(json.dumps(cat), encoding="utf-8")

    pub = published_catalog(tmp_path, "http://paul:7477/")
    assert {b["slug"] for b in pub["books"]} == {"calibre-35"}  # unrendered wl-x excluded
    assert pub["books"][0]["chapters"] == ["http://paul:7477/calibre-35/001.mp3"]


def test_pull_catalog_merges_peer_and_keeps_local(tmp_path):
    local = tmp_path / "catalog.json"
    local.write_text(json.dumps({"version": 1, "books": [{"slug": "wl-local", "title": "Local"}]}),
                     encoding="utf-8")
    mesh = Mesh({"nodes": {"paul": {"host": "p", "resources": {
        "media": {"audiobooks": {"kind": "http", "url": "http://p:7477/"}}}}}}, self_node="jessica")

    class _Resp:
        def __init__(self, body): self._b = body
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return self._b

    def opener(url, timeout=0):
        assert url == "http://p:7477/catalog.json"  # trailing slash normalised
        return _Resp(json.dumps({"books": [
            {"slug": "calibre-35", "title": "Metro",
             "chapters": ["http://p:7477/calibre-35/001.mp3"]}]}).encode("utf-8"))

    out = pull_catalog(node="jessica", local_catalog=str(local), mesh=mesh, opener=opener)
    merged = json.loads(local.read_text(encoding="utf-8"))
    assert {b["slug"] for b in merged["books"]} == {"wl-local", "calibre-35"}
    assert out["merged"] == 1
