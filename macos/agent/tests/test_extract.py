from rachel.extract import text_to_chapters


def test_text_split_on_headings():
    txt = "ROZDZIAŁ I\n\nAla ma kota.\n\nROZDZIAŁ II\n\nKot ma Alę.\n"
    chs = text_to_chapters(txt)
    assert [c.index for c in chs] == [0, 1]
    assert "Ala ma kota" in chs[0].text
    assert chs[1].title.startswith("ROZDZIAŁ II")


def test_no_headings_single_chapter():
    chs = text_to_chapters("Zwykły tekst bez rozdziałów.")
    assert len(chs) == 1
    assert chs[0].text == "Zwykły tekst bez rozdziałów."


def test_long_chapter_is_wrapped():
    para = ("Zdanie. " * 400).strip()             # ~3200 chars
    txt = f"ROZDZIAŁ I\n\n{para}\n\n{para}\n"       # ~6400 chars under one heading
    chs = text_to_chapters(txt, max_chars=4000)
    assert len(chs) >= 2
    assert all(len(c.text) <= 4000 for c in chs)
    # continuation chapters keep the heading with a suffix
    assert chs[1].title.startswith("ROZDZIAŁ I (")
