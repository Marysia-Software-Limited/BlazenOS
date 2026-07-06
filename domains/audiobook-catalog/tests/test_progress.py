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


def test_empty_slug_is_ignored(tmp_path):
    a = AudiobookProgress(path=str(tmp_path / "p.json"))
    a.save("", chapter=1, offset_s=1.0)
    assert a.get("") is None


def test_negative_values_are_clamped(tmp_path):
    a = AudiobookProgress(path=str(tmp_path / "p.json"))
    a.save("s", chapter=-3, offset_s=-9.0)
    assert a.get("s")["chapter"] == 0
    assert a.get("s")["offset_s"] == 0.0
