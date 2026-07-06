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
        INSERT INTO data VALUES (7, 'MOBI', 'Pan Tadeusz - Adam Mickiewicz');
        INSERT INTO books VALUES (9, 'Some English Book', 'X/Y (9)');
        INSERT INTO books_languages_link VALUES (9, 2);
        """
    )
    con.commit()
    con.close()
    (tmp_path / "Adam Mickiewicz" / "Pan Tadeusz (7)").mkdir(parents=True)
    return CalibreLibrary(db_path=str(db), library_root=str(tmp_path))


def test_polish_books_filters_language(lib):
    books = lib.polish_books()
    assert [b.id for b in books] == [7]
    assert books[0].author == "Adam Mickiewicz"
    assert books[0].slug == "calibre-7"
    assert set(books[0].formats) == {"EPUB", "MOBI"}


def test_resolve_by_title(lib):
    assert lib.resolve("pan tadeusz").id == 7


def test_resolve_unknown_is_none(lib):
    assert lib.resolve("zzzz nieistnieje") is None


def test_format_file_locates_epub(lib, tmp_path):
    book = lib.polish_books()[0]
    epub = tmp_path / "Adam Mickiewicz" / "Pan Tadeusz (7)" / "Pan Tadeusz.epub"
    epub.write_bytes(b"x")
    assert book.format_file(str(tmp_path), "EPUB") == epub
    assert book.format_file(str(tmp_path), "AZW3") is None
