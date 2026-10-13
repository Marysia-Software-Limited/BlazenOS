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


def test_default_path_follows_data_dir(monkeypatch, tmp_path):
    """Desktop + appliance installs set BLAZEN_DATA_DIR; progress lives beside
    memory.json there (the file the fabric syncs), not under /var/lib/blazen."""
    monkeypatch.delenv("BLAZEN_AUDIOBOOK_PROGRESS", raising=False)
    monkeypatch.setenv("BLAZEN_DATA_DIR", str(tmp_path / "data"))
    a = AudiobookProgress()
    assert a.path == tmp_path / "data" / "progress.json"
    a.save("calibre-35", chapter=3, offset_s=42.0, title="Metro 2033", updated="2026-10-12T16:00")
    assert (tmp_path / "data" / "progress.json").exists()
    assert AudiobookProgress().get("calibre-35")["offset_s"] == 42.0


def test_explicit_env_path_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("BLAZEN_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("BLAZEN_AUDIOBOOK_PROGRESS", str(tmp_path / "x" / "p.json"))
    assert AudiobookProgress().path == tmp_path / "x" / "p.json"


def test_unwritable_store_warns_instead_of_hiding(monkeypatch, tmp_path, caplog):
    monkeypatch.delenv("BLAZEN_AUDIOBOOK_PROGRESS", raising=False)
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    a = AudiobookProgress(path=str(blocker / "progress.json"))
    with caplog.at_level("WARNING", logger="audiobook_catalog.progress"):
        a.save("s", chapter=1, offset_s=2.0)
    assert "audiobook progress not saved" in caplog.text
