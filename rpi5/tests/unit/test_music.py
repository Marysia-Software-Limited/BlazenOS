"""Tier 0 — offline music directory: album-queue resolution (2026-07-21).

"Zagraj ballady morderców" used to play ONE random track of the album and go
silent. `resolve_album` now turns an album-naming request into the ordered
track list of the most complete single rip, while a bare artist request keeps
its random-track behaviour. Runs against a synthetic index in tmp_path (the
loader drops files under ~16 kB as dead rips, so fixtures are padded).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.music import MusicDirectory
from blazend.domains.ai_orchestrator.adapters.rpi5.tools import Tools

_PAD = b"\0" * (20 * 1024)  # past the dead-rip size floor


def _index(tmp: Path) -> Path:
    """Two rips of the same album (one complete, one partial) + an artist pool,
    mirroring the live library's duplicate-rip mess (incl. a mojibake tag)."""
    tracks = []

    def add(folder: str, fname: str, title: str, artist: str, album: str,
            track: int = 0) -> None:
        p = tmp / folder / fname
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(_PAD)
        tracks.append({"title": title, "artist": artist, "album": album,
                       "track": track, "path": str(p)})

    # equally-complete but UNTAGGED rip, listed FIRST — the tie-break must
    # prefer the numbered rip below, not insertion order (live bug 2026-07-21:
    # an untagged 10-track rip shadowed the fully numbered one).
    add("stara kaseta/ballady", "Bar u O'Malley'a.mp3", "Bar u O'Malley'a", "", "Ballady Morderców")
    add("stara kaseta/ballady", "Crow Jane.mp3", "Crow Jane", "", "Ballady Morderców")
    add("stara kaseta/ballady", "Deszczowy Klown.mp3", "Deszczowy Klown", "", "Ballady Morderców")
    # complete rip — flat folder, mojibake album tag, UNNUMBERED filenames whose
    # ID3 track numbers disagree with alphabetical order (the live rips do this)
    add("ballady mordercow", "Piesn o radosci.mp3", "Piesn o radosci", "Kinga Preis", "Ballady Morderc�w", track=1)
    add("ballady mordercow", "Stagger Lee.mp3", "Stagger Lee", "Maciej Maleńczuk", "Ballady Morderc�w", track=2)
    add("ballady mordercow", "Henry Lee.mp3", "Henry Lee", "Kazik Staszewski", "Ballady Morderc�w", track=3)
    # partial rip of the same album — different folder + clean tag
    add("Nick Cave/Ballady Morderców", "Henry Lee.mp3", "Henry Lee", "Kazik Staszewski", "Ballady Morderców")
    add("Nick Cave/Ballady Morderców", "Istny Cud.mp3", "Istny Cud", "Renata Przemyk", "Ballady Morderców")
    # artist pool with its own album
    add("kazik", "01 Wewnetrzne sprawy.mp3", "Wewnetrzne sprawy", "Kazik", "Tata Kazika")
    add("kazik", "02 Piosenka mlodych wioslarzy.mp3", "Piosenka mlodych wioslarzy", "Kazik", "Tata Kazika")

    idx = tmp / "index.json"
    idx.write_text(json.dumps({"version": 1, "tracks": tracks}), encoding="utf-8")
    return idx


@pytest.fixture
def music(tmp_path: Path) -> MusicDirectory:
    return MusicDirectory(index_path=str(_index(tmp_path)))


def test_album_request_returns_the_complete_rip_in_track_order(music):
    q = music.resolve_album("ballady morderców")
    assert q is not None and len(q) == 3  # the 3-track rip beats the 2-track one
    # ID3 track numbers order the album, NOT the alphabetical filenames.
    assert [Path(t.path).name for t in q] == [
        "Piesn o radosci.mp3", "Stagger Lee.mp3", "Henry Lee.mp3"]
    assert len({Path(t.path).parent for t in q}) == 1  # one rip, never a mix


def test_album_tag_alone_is_enough(music):
    # "tata kazika" names no artist wholesale, but is the pool's album tag.
    q = music.resolve_album("tata kazika")
    assert q is not None and [t.title for t in q] == [
        "Wewnetrzne sprawy", "Piosenka mlodych wioslarzy"]


def test_artist_request_stays_a_random_track(music):
    # "zagraj Kazika" must keep surprising — no queue for a bare artist.
    assert music.resolve_album("kazika") is None
    t = music.resolve("kazika")
    assert t is not None and "Kazik" in t.artist  # pools Kazik AND Kazik Staszewski


def test_random_words_never_queue(music):
    assert music.resolve_album("coś") is None
    assert music.resolve_album("") is None


def test_single_track_match_is_not_an_album(music):
    # A title that happens to sit in a folder alone is a track, not a queue.
    assert music.resolve_album("istny cud") is None


def test_tools_album_payload_is_an_ordered_playlist(music):
    belt = Tools(music=music)
    r = belt.music_play("ballady morderców.", "pl")  # whisper keeps the period
    assert r.action == "music_play"
    assert r.payload["is_playlist"] is True and r.payload["chapter"] == 0
    assert len(r.payload["chapters"]) == 3
    assert r.payload["path"] == r.payload["chapters"][0]
    # Spoken confirmation uses the user's words (tags are mojibake) + PL plural.
    assert "ballady morderców" in r.text and "3 utwory" in r.text


def test_tools_artist_payload_stays_single_track(music):
    r = Tools(music=music).music_play("kazika", "pl")
    assert r.action == "music_play"
    assert "is_playlist" not in r.payload and "chapters" not in r.payload
