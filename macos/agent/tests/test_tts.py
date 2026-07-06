from pathlib import Path

from rachel.tts import apple_commands


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
