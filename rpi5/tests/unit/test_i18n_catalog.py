"""Tier 0 — the message catalog contract (docs/13-LANGUAGES.md §6, finally real).

Every user-facing string lives in ``configs/phrases.yaml``; this suite is the
parity gate: each key must carry BOTH ``pl`` and ``en`` (Polish-only runtime,
EN retained as an asset), placeholders must match across languages, and the
``blazend.i18n`` API must degrade safely on gaps. Offline, repo configs only.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from blazend.i18n import Catalog, _plural_category

REPO = Path(__file__).resolve().parents[3]
CATALOG_FILE = REPO / "configs" / "phrases.yaml"

REQUIRED_LANGS = ("pl", "en")  # Polish-first; EN is a retained asset
_PLACEHOLDER = re.compile(r"\{(\w+)[^}]*\}")


def _load() -> dict:
    return yaml.safe_load(CATALOG_FILE.read_text(encoding="utf-8")) or {}


def _lang_entries() -> list[tuple[str, dict]]:
    """Every (dotted-key, {lang: value}) entry across all catalog groups."""
    data = _load()
    out: list[tuple[str, dict]] = []
    for group in ("cues", "messages", "prompts", "plurals"):
        for key, entry in (data.get(group, {}) or {}).items():
            out.append((f"{group}.{key}", entry))
    for name, table in (data.get("tables", {}) or {}).items():
        for key, entry in (table or {}).items():
            out.append((f"tables.{name}.{key}", entry))
    return out


def _placeholders(value) -> set[str]:
    if isinstance(value, dict):  # plural forms — union over forms
        return {p for v in value.values() for p in _PLACEHOLDER.findall(str(v))}
    return set(_PLACEHOLDER.findall(str(value)))


# ----- catalog contract -------------------------------------------------------


def test_catalog_exists_and_is_versioned():
    data = _load()
    assert int(data.get("version", 0)) >= 2
    assert data.get("cues"), "v1 cues must survive (voicebank + supervisor)"


def test_every_entry_has_pl_and_en():
    missing = [key for key, entry in _lang_entries()
               if not isinstance(entry, dict)
               or any(lang not in entry or entry[lang] in (None, "", {})
                      for lang in REQUIRED_LANGS)]
    assert not missing, f"keys missing pl/en parity: {missing[:10]}"


def test_polish_leads_every_entry():
    # Polish-first (docs/13): pl is listed before en in each entry.
    for key, entry in _lang_entries():
        langs = list(entry)
        assert langs.index("pl") < langs.index("en"), f"{key}: pl must lead"


def test_placeholders_match_across_languages():
    bad = []
    for key, entry in _lang_entries():
        if not isinstance(entry, dict):
            continue
        ph = {lang: _placeholders(entry[lang]) for lang in REQUIRED_LANGS
              if lang in entry}
        if len(set(map(frozenset, ph.values()))) > 1:
            bad.append((key, ph))
    assert not bad, f"placeholder mismatch between languages: {bad[:5]}"


def test_no_recognition_data_in_catalog():
    # Trigger regexes must stay with their matchers — a translator editing
    # `(ł|l)` alternations would silently break intent matching.
    for key, entry in _lang_entries():
        for lang in REQUIRED_LANGS:
            val = entry.get(lang)
            if isinstance(val, str):
                assert "(ł|l)" not in val and "?P<" not in val, \
                    f"{key} looks like a recognition regex"


# ----- API behaviour ----------------------------------------------------------


@pytest.fixture()
def cat() -> Catalog:
    return Catalog(data={
        "cues": {"listening": {"pl": "Słucham?", "en": "Yes?"}},
        "messages": {
            "radio.station_on": {"pl": "Włączam stację {station}.",
                                 "en": "Turning on {station}."},
            "pl_only": {"pl": "Tylko po polsku."},
        },
        "plurals": {
            "tracks": {"pl": {"one": "1 utwór", "few": "{n} utwory",
                              "many": "{n} utworów"},
                       "en": {"one": "1 track", "other": "{n} tracks"}},
        },
        "tables": {"days": {"0": {"pl": "poniedziałek", "en": "Monday"}}},
    })


def test_msg_lookup_and_interpolation(cat):
    assert cat.msg("radio.station_on", "pl", station="Trójka") == "Włączam stację Trójka."
    assert cat.msg("radio.station_on", "en", station="Trójka") == "Turning on Trójka."
    assert cat.msg("cues.listening", "pl") == "Słucham?"


def test_msg_falls_back_language_then_key(cat):
    assert cat.msg("pl_only", "en") == "Tylko po polsku."  # en → pl fallback
    assert cat.msg("does.not.exist", "pl") == "does.not.exist"


def test_polish_plural_categories():
    assert _plural_category(1, "pl") == "one"
    assert [_plural_category(n, "pl") for n in (2, 3, 4)] == ["few"] * 3
    assert [_plural_category(n, "pl") for n in (5, 11, 12, 14, 21, 25)] == \
        ["many"] * 6
    assert _plural_category(22, "pl") == "few"  # 22 → few (not in 12-14)
    assert _plural_category(1, "en") == "one"
    assert _plural_category(7, "en") == "other"


def test_plural_renders_forms(cat):
    assert cat.plural("tracks", 1, "pl") == "1 utwór"
    assert cat.plural("tracks", 3, "pl") == "3 utwory"
    assert cat.plural("tracks", 7, "pl") == "7 utworów"
    assert cat.plural("tracks", 2, "en") == "2 tracks"


def test_table_lookup(cat):
    assert cat.table("days", 0, "pl") == "poniedziałek"
    assert cat.table("days", 0, "en") == "Monday"
    assert cat.table("days", 9, "pl", default="?") == "?"
