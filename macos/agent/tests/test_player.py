import json
from pathlib import Path

from audiobook_catalog.progress import AudiobookProgress

import rachel.player as pl
from rachel.player import latest_slug, play_book, play_book_argv, read_position


def _fake_player(seconds: float, done: bool):
    def run(argv, check=False):  # noqa: ARG001
        pf = argv[argv.index("--position-file") + 1]
        Path(pf).write_text(json.dumps({"seconds": seconds, "done": done}))
        class R:
            returncode = 0
        return R()
    return run


def test_play_book_argv_resume_and_compress():
    argv = play_book_argv("/bin/rachel-player", "/a/01.mp3",
                          start_seconds=9.0, position_file="/p/pos.json")
    assert argv[0] == "/bin/rachel-player"
    assert argv[1] == "/a/01.mp3"
    assert "--start-seconds" in argv and "9.0" in argv
    assert argv[argv.index("--position-file") + 1] == "/p/pos.json"
    assert "--compress" in argv


def test_play_book_argv_no_seek_at_start():
    argv = play_book_argv("/bin/rp", "/a/00.mp3", start_seconds=0.0,
                          position_file="/p/pos.json")
    assert "--start-seconds" not in argv


def test_read_position(tmp_path):
    p = tmp_path / "pos.json"
    p.write_text('{"seconds":42.5,"done":true}')
    assert read_position(str(p)) == (42.5, True)
    assert read_position(str(tmp_path / "missing.json")) == (0.0, False)


def test_latest_slug(tmp_path):
    p = tmp_path / "progress.json"
    p.write_text(json.dumps({
        "calibre-7": {"chapter": 1, "offset_s": 3.0, "updated": "2026-07-06T09:00"},
        "calibre-9": {"chapter": 0, "offset_s": 0.0, "updated": "2026-07-06T11:00"},
    }))
    assert latest_slug(str(p)) == "calibre-9"
    assert latest_slug(str(tmp_path / "none.json")) is None


def test_play_book_autoadvances_and_clears_when_finished(tmp_path, monkeypatch):
    monkeypatch.setattr(pl.subprocess, "run", _fake_player(10.0, done=True))
    prog = AudiobookProgress(path=str(tmp_path / "progress.json"))
    play_book(["/a/00.mp3", "/a/01.mp3"], "calibre-7", "Book",
              progress=prog, now="t", binary="/x")
    # every chapter ended naturally → book finished → progress cleared
    assert prog.get("calibre-7") is None


def test_play_book_saves_resume_on_incomplete(tmp_path, monkeypatch):
    monkeypatch.setattr(pl.subprocess, "run", _fake_player(5.0, done=False))
    prog = AudiobookProgress(path=str(tmp_path / "progress.json"))
    play_book(["/a/00.mp3", "/a/01.mp3"], "calibre-7", "Book",
              progress=prog, now="t", binary="/x")
    got = prog.get("calibre-7")
    assert got["chapter"] == 0 and got["offset_s"] == 5.0
