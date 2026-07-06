import json
from pathlib import Path

import rachel.tts as tts_mod
from rachel.tts import XttsTTS, apple_commands


def test_apple_commands_shape():
    say, conv = apple_commands(Path("/t/ch.txt"), Path("/t/ch.aiff"),
                               Path("/t/07.mp3"), "Zosia")
    assert say[:5] == ["say", "-v", "Zosia", "-f", "/t/ch.txt"]
    assert "-o" in say and "/t/ch.aiff" in say
    assert conv[0] == "ffmpeg" and conv[-1] == "/t/07.mp3"
    assert "/t/ch.aiff" in conv


def test_apple_commands_honours_voice():
    say, _ = apple_commands(Path("/t/a.txt"), Path("/t/a.aiff"),
                            Path("/t/00.mp3"), "Krzysztof")
    assert say[2] == "Krzysztof"


def test_xtts_posts_text_and_writes_mp3(tmp_path, monkeypatch):
    sent = {}

    class FakeResp:
        def read(self):
            return b"RIFFfakewav"
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=0):
        sent["url"] = req.full_url
        sent["payload"] = json.loads(req.data.decode())
        return FakeResp()

    def fake_run(argv, check=False, capture_output=False):
        # simulate ffmpeg: the wav input exists, write the mp3 output
        assert argv[0] == "ffmpeg"
        Path(argv[-1]).write_bytes(b"ID3mp3")
        sent["ffmpeg_in"] = argv[argv.index("-i") + 1]
        class R:
            returncode = 0
        return R()

    # the backend lazily imports urllib.request inside render_chapter
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(tts_mod.subprocess, "run", fake_run)

    out = tmp_path / "05.mp3"
    XttsTTS("http://paul:8091/synthesize", speaker="Ana Florence").render_chapter("Ala ma kota.", out)

    assert sent["url"] == "http://paul:8091/synthesize"
    assert sent["payload"] == {"text": "Ala ma kota.", "language": "pl", "speaker": "Ana Florence"}
    assert sent["ffmpeg_in"].endswith("05.wav")
    assert out.read_bytes() == b"ID3mp3"
