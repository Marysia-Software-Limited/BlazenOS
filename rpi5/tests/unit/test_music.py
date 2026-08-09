"""Tier 0 — offline music directory: album/artist queue resolution.

"Zagraj ballady morderców" used to play ONE random track of the album and go
silent. An album request now queues the ordered track list of the most
complete single rip; an artist request queues their whole catalogue shuffled
(decision 2026-07-27: play everything until "stop"). Runs against a synthetic
index in tmp_path (the loader drops files under ~16 kB as dead rips, so
fixtures are padded).
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


def test_artist_request_queues_their_whole_catalogue_shuffled(music):
    # Decision 2026-07-27: "zagraj Kazika" plays EVERYTHING of the artist
    # until "stop" — a shuffled queue, deduped across duplicate rips.
    assert music.resolve_album("kazika") is None  # not an album-level match
    q = music.resolve_artist("kazika")
    assert q is not None
    titles = [t.title for t in q]
    assert sorted(set(titles)) == sorted(titles)  # duplicate rips deduped
    assert set(titles) == {"Wewnetrzne sprawy", "Piosenka mlodych wioslarzy", "Henry Lee"}


def test_explicit_all_request_queues(music):
    # "zagraj całego Kazika" — same catalogue queue via the explicit modifier.
    q = music.resolve_all("całego kazika")
    assert q is not None and len(q) == 3
    # "zagraj wszystko" → the whole library, shuffled + deduped.
    lib = music.resolve_all("wszystko")
    assert lib is not None and len(lib) >= 6


def test_something_request_shuffles_the_library(music):
    # "zagraj coś" keeps playing until "stop" too — a library shuffle,
    # not one random track.
    q = music.resolve_all("coś")
    assert q is not None and len(q) >= 6


def test_album_filler_word_is_stripped(music):
    # "zagraj album ballady morderców" — "album" is filler, not the name.
    q = music.resolve_album("album ballady morderców")
    assert q is not None and len(q) == 3


def test_single_track_match_is_not_an_album(music):
    # A title that happens to sit in a folder alone is a track, not a queue.
    assert music.resolve_album("istny cud") is None
    assert music.resolve_artist("istny cud") is None


def test_tools_album_payload_is_an_ordered_playlist(music):
    belt = Tools(music=music)
    r = belt.music_play("ballady morderców.", "pl")  # whisper keeps the period
    assert r.action == "music_play"
    assert r.payload["is_playlist"] is True and r.payload["chapter"] == 0
    assert len(r.payload["chapters"]) == 3
    assert r.payload["path"] == r.payload["chapters"][0]
    # Spoken confirmation uses the user's words (tags are mojibake) + PL plural.
    assert "ballady morderców" in r.text and "3 utwory" in r.text


def test_tools_artist_payload_is_a_shuffled_playlist(music):
    r = Tools(music=music).music_play("kazika", "pl")
    assert r.action == "music_play"
    assert r.payload["is_playlist"] is True and len(r.payload["chapters"]) == 3
    assert "losowej" in r.text  # shuffled confirmation, count included


def test_tools_title_payload_stays_single_track(music):
    r = Tools(music=music).music_play("istny cud", "pl")
    assert r.action == "music_play"
    assert "is_playlist" not in r.payload and "chapters" not in r.payload


def test_fast_path_reply_envelope_carries_the_queue(music):
    # The seam that actually broke live (2026-07-27): tools built a correct
    # queue payload, but the supervisor's reply-envelope builder re-created the
    # payload and forwarded only audiobook queues — every album/artist queue
    # silently became a single track ("następny" replayed track 1 forever).
    from types import SimpleNamespace

    from blazend.domains.ai_orchestrator.adapters.rpi5.dispatch import DispatchResult
    from blazend.domains.systems.adapters.rpi5.orchestrator.supervisor import Orchestrator
    from blazend.events import Envelope

    belt = Tools(music=music)

    class _Disp:
        def dispatch(self, intent: str, params: dict, lang: str) -> DispatchResult:
            res = belt.music_play(str(params.get("query", "")), lang)
            # Mirrors dispatch.py's tool-call flattening: payload merges into data.
            return DispatchResult(res.text, lang, "tool",
                                  data={"tool": "music.play", "ok": res.ok, **res.payload})

    stub = SimpleNamespace(_dispatcher=_Disp(), _radio=SimpleNamespace(playing=False),
                           _music_enabled=True, _book=None, _last_book=None,
                           _last_source="", _last_source_name="")
    env = Envelope(topic="nlu.intent", source="test", data={
        "intent": "music_play", "params": {"query": "ballady morderców"}, "language": "pl"})
    reply = Orchestrator._dispatch_intent(stub, env)  # unbound: the stub is `self`
    assert reply is not None and reply.data["action"] == "music_play"
    payload = reply.data["payload"]
    assert payload["is_playlist"] is True and payload["chapter"] == 0
    assert len(payload["chapters"]) == 3


def test_tools_album_payload_carries_spoken_labels(music):
    # The queue payload ships the index's repaired titles so now-playing /
    # shuffle announcements never read a mojibake filename aloud. A various-
    # artists album names the artist per track.
    r = Tools(music=music).music_play("ballady morderców", "pl")
    assert r.payload["labels"] == [
        "Kinga Preis — Piesn o radosci",
        "Maciej Maleńczuk — Stagger Lee",
        "Kazik Staszewski — Henry Lee",
    ]


def test_tools_artist_queue_labels_follow_the_shuffle(music):
    # The pool spans two artist strings (Kazik / Kazik Staszewski) → prefixed.
    # Whatever the shuffle order, label i must name the file at chapter i.
    r = Tools(music=music).music_play("kazika", "pl")
    assert len(r.payload["labels"]) == len(r.payload["chapters"]) == 3
    by_file = {
        "01 Wewnetrzne sprawy.mp3": "Kazik — Wewnetrzne sprawy",
        "02 Piosenka mlodych wioslarzy.mp3": "Kazik — Piosenka mlodych wioslarzy",
        "Henry Lee.mp3": "Kazik Staszewski — Henry Lee",
    }
    assert [by_file[Path(p).name] for p in r.payload["chapters"]] == r.payload["labels"]


def test_queue_label_prefers_payload_label_over_filename():
    from blazend.domains.systems.adapters.rpi5.orchestrator.supervisor import _queue_label
    book = {"chapters": ["/m/03 Pie�� o mi�o�ci.mp3", "/m/04 druga.mp3"],
            "labels": ["Pieśń o miłości", ""]}
    assert _queue_label(book, 0) == "Pieśń o miłości"
    assert _queue_label(book, 1) == "druga"  # empty label → filename stem


@pytest.mark.asyncio
async def test_shuffle_keeps_labels_aligned_with_paths():
    from types import SimpleNamespace

    from blazend.domains.systems.adapters.rpi5.orchestrator.supervisor import Orchestrator
    paths = [f"/m/{i:02d} plik{i}.mp3" for i in range(6)]
    labels = [f"Tytuł {i}" for i in range(6)]
    book = {"chapters": list(paths), "labels": list(labels), "index": 2,
            "kind": "album", "name": "x"}
    spoken: list[str] = []

    async def _sp(text: str, lang: str = "pl") -> None:
        spoken.append(text)

    stub = SimpleNamespace(_book=book, _radio=SimpleNamespace(playing=True),
                           _speak_over_playback=_sp)
    await Orchestrator._shuffle_queue(stub, "pl")
    # Current track stays first; every position still names its own file.
    assert book["chapters"][0] == paths[2] and book["labels"][0] == labels[2]
    original = dict(zip(paths, labels, strict=True))
    assert [original[p] for p in book["chapters"]] == book["labels"]
    assert spoken and book["labels"][1] in spoken[0]  # announce uses the label


def test_album_nav_forwards_labels():
    from types import SimpleNamespace

    from blazend.domains.systems.adapters.rpi5.orchestrator.supervisor import Orchestrator
    stub = SimpleNamespace(_book={"chapters": ["/a.mp3", "/b.mp3"],
                                  "labels": ["Utwór A", "Utwór B"],
                                  "index": 0, "kind": "album", "name": "x"})
    env = Orchestrator._album_nav(stub, +1)
    assert env.data["payload"]["labels"] == ["Utwór A", "Utwór B"]
    assert env.data["payload"]["chapter"] == 1


def test_fast_path_envelope_forwards_labels(music):
    # Same seam as test_fast_path_reply_envelope_carries_the_queue — labels
    # must survive the payload rebuild too, or every queue speaks filenames.
    from types import SimpleNamespace

    from blazend.domains.ai_orchestrator.adapters.rpi5.dispatch import DispatchResult
    from blazend.domains.systems.adapters.rpi5.orchestrator.supervisor import Orchestrator
    from blazend.events import Envelope

    belt = Tools(music=music)

    class _Disp:
        def dispatch(self, intent: str, params: dict, lang: str) -> DispatchResult:
            res = belt.music_play(str(params.get("query", "")), lang)
            return DispatchResult(res.text, lang, "tool",
                                  data={"tool": "music.play", "ok": res.ok, **res.payload})

    stub = SimpleNamespace(_dispatcher=_Disp(), _radio=SimpleNamespace(playing=False),
                           _music_enabled=True, _book=None, _last_book=None,
                           _last_source="", _last_source_name="")
    env = Envelope(topic="nlu.intent", source="test", data={
        "intent": "music_play", "params": {"query": "ballady morderców"}, "language": "pl"})
    reply = Orchestrator._dispatch_intent(stub, env)
    assert reply is not None
    payload = reply.data["payload"]
    assert len(payload["labels"]) == len(payload["chapters"]) == 3
    assert payload["labels"][0] == "Kinga Preis — Piesn o radosci"


def test_indexer_demojibake_repairs_cp1250_tags():
    # The indexer un-mangles cp1250 tags mis-decoded as latin-1/cp1252 by old
    # rippers, and leaves clean/foreign text alone.
    import importlib.util
    repo = Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location("index_music", repo / "scripts" / "index-music.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fix = mod.demojibake
    assert fix("Przekleñstwo Millhoven") == "Przekleństwo Millhoven"
    assert fix("Kinga Preis & Mariusz Drê¿ka") == "Kinga Preis & Mariusz Drężka"
    assert fix("Spalaj Siê") == "Spalaj Się"
    assert fix("Go\x9ccie") == "Goście"
    assert fix("Przekleństwo") == "Przekleństwo"  # already correct → untouched
    assert fix("Boże, coś Polskę") == "Boże, coś Polskę"
    assert fix("plain ascii") == "plain ascii"
    # Artist fallback must NOT become the album name (an album-titled folder
    # says nothing about who plays; live 2026-07-27 it turned album requests
    # into shuffled artist requests).
    src = Path("/lib")
    d = mod.derive(src / "Ballady Morderców" / "01 Song.mp3", src, {})
    assert d["album"] == "Ballady Morderców" and d["artist"] == ""
    d = mod.derive(src / "kazik" / "01 Wewnetrzne sprawy.mp3", src,
                   {"album": "Tata Kazika"})
    assert d["artist"] == "kazik"  # band folder still stands in for a lost tag
