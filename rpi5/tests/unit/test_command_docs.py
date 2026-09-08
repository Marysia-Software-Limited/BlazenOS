"""Tier 0 — the CLOSED command list is a documented contract (2026-09-08):
every intent in configs/intents/system.yaml must have its page under
docs/commands/ and a row in 00-INDEX.md. Adding a voice feature without
documenting it fails this test; regenerate with scripts/gen-command-docs.py."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]
DOCS = REPO / "docs" / "commands"


def _intent_names() -> list[str]:
    data = yaml.safe_load(
        (REPO / "configs" / "intents" / "system.yaml").read_text(encoding="utf-8"))
    return [str(it["name"]) for it in data.get("intents", [])]


def test_every_intent_has_a_command_doc():
    missing = [n for n in _intent_names() if not (DOCS / f"{n}.md").exists()]
    assert not missing, (
        f"intents without docs/commands page: {missing} — run "
        "scripts/gen-command-docs.py (and add a DESC entry)")


def test_index_lists_every_intent():
    index = (DOCS / "00-INDEX.md").read_text(encoding="utf-8")
    missing = [n for n in _intent_names() if f"`{n}`" not in index]
    assert not missing, f"intents missing from 00-INDEX.md: {missing}"


def test_ai_query_is_documented():
    # The single LLM door of the closed list must stay documented.
    assert (DOCS / "ai_query.md").exists()
    assert "sprawdź" in (DOCS / "ai_query.md").read_text(encoding="utf-8")
