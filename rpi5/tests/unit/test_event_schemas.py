"""Tier 0 — every IPC event topic has a JSON Schema and the schema
round-trips through Python's :class:`Envelope`.

This is the gate that enforces the cross-language IPC contract described
in docs/14-RUST-PYTHON-SPLIT.md §3.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# configs/ and scripts/ are shared and stay at the repo root (parents[3]),
# above the rpi5/ appliance project that holds these tests.
REPO = Path(__file__).resolve().parents[3]
SCHEMAS = REPO / "configs" / "_schema" / "events"
sys.path.insert(0, str(REPO / "scripts"))


def test_schemas_dir_exists():
    assert SCHEMAS.is_dir(), f"expected {SCHEMAS}"


@pytest.mark.parametrize(
    "topic",
    sorted(
        {
            "audio.frame",
            "wake.detected",
            "vad.start",
            "vad.end",
            "asr.partial",
            "asr.final",
            "nlu.intent",
            "brain.reply",
            "tts.frame",
            "system.event",
            "error",
            # Fabric (multi-device) topics — see docs/product/11-FABRIC.md.
            "fabric.peer_online",
            "fabric.peer_offline",
            "fabric.sync_fact",
            "fabric.rpc_request",
            "fabric.rpc_response",
        }
    ),
)
def test_each_topic_has_schema(topic: str):
    path = SCHEMAS / f"{topic}.schema.json"
    assert path.is_file(), f"missing schema for {topic}"
    data = json.loads(path.read_text())
    assert data.get("title") == topic
    assert data["properties"]["topic"]["const"] == topic


def test_validator_runs_end_to_end():
    # The full validator + round-trip lives in scripts/gen-event-types.py.
    # Invoking it directly here ensures CI catches schema drift.
    from importlib import util

    mod_path = REPO / "scripts" / "gen-event-types.py"
    spec = util.spec_from_file_location("gen_event_types", mod_path)
    assert spec is not None
    assert spec.loader is not None
    mod = util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    schemas = mod.load_schemas(SCHEMAS)
    errors = []
    errors.extend(mod.validate_schemas(schemas))
    errors.extend(mod.coverage_errors(schemas))
    for topic, schema in schemas.items():
        if (err := mod.roundtrip_envelope(topic, schema)) is not None:
            errors.append(err)
    assert not errors, "schema validation failed:\n  " + "\n  ".join(errors)
