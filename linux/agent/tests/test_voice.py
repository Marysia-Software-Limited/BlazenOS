"""Unit tests for the Linux node's TTS render+play (no network, no audio)."""
from __future__ import annotations

from mesh_registry import Mesh

from jessica_linux.voice import Voice


def _mesh(url: str = "http://192.168.50.102:8091/synthesize") -> Mesh:
    data = {
        "nodes": {
            "paul": {
                "host": "192.168.50.102",
                "resources": {
                    "tts": {"xtts": {"kind": "xtts", "url": url,
                                     "language": "pl", "speaker": "Ana Florence"}}
                },
            }
        }
    }
    return Mesh(data, self_node="paul")


class _FakeResp:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self) -> bytes:
        return self._body


def test_render_posts_to_mesh_tts_with_language_and_speaker():
    captured = {}

    def opener(req, timeout=0):
        captured["url"] = req.full_url
        captured["body"] = req.data
        captured["timeout"] = timeout
        return _FakeResp(b"RIFF....WAVEfake")

    v = Voice(mesh=_mesh(), opener=opener, runner=lambda *a, **k: None)
    assert v.available
    wav = v.render("Dzień dobry")
    assert wav == b"RIFF....WAVEfake"
    assert captured["url"] == "http://192.168.50.102:8091/synthesize"
    assert b"Dzie" in captured["body"] and b'"language": "pl"' in captured["body"]
    assert b"Ana Florence" in captured["body"]


def test_speak_renders_then_plays_the_wav_with_compression():
    calls = {}

    def runner(argv, **kw):
        calls["argv"] = argv
        return None

    v = Voice(mesh=_mesh(), opener=lambda req, timeout=0: _FakeResp(b"wavbytes"), runner=runner)
    v.speak("cześć", device="plughw:CARD=H3,DEV=0")
    argv = calls["argv"]
    assert argv[0].endswith("blazend-player")
    assert "--source" in argv and "--compress" in argv  # leveling is on by default
    assert "--level" not in argv  # not a real flag; only --no-level exists
    assert argv[argv.index("--device") + 1] == "plughw:CARD=H3,DEV=0"
    # the played file is the rendered WAV, cleaned up after
    src = argv[argv.index("--source") + 1]
    assert src.endswith(".wav")


def test_no_tts_resource_is_unavailable():
    empty = Mesh({"nodes": {"paul": {"host": "h", "resources": {}}}}, self_node="paul")
    assert Voice(mesh=empty, opener=lambda *a, **k: None, runner=lambda *a, **k: None).available is False
