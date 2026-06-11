#!/usr/bin/env python3
"""scripts/gen-event-types.py — IPC schema validation + (future) code-gen.

M1 scope: validate that every event topic the Python/Rust hand-written
types claim to support has a schema, and that the schemas are valid JSON
Schema draft-07. Round-trips a synthetic example through Python's
:class:`Envelope` and asserts it validates.

M2 scope: replace the placeholder generators (``--python-out`` and
``--rust-out``) with real code generation using
``datamodel-code-generator`` and ``typify``. The CLI shape is stable so
the Makefile target stays the same.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from jsonschema import Draft7Validator
except ImportError:
    Draft7Validator = None  # type: ignore[assignment, misc]


REPO = Path(__file__).resolve().parents[1]

# Mirror this set against blazend.events.ALL_TOPICS (Python) and
# Topic::as_str() (Rust). Used to fail when a topic is added without a
# schema.
EXPECTED_TOPICS: set[str] = {
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
    # Fabric topics — multi-device Jessica. See docs/product/11-FABRIC.md.
    "fabric.peer_online",
    "fabric.peer_offline",
    "fabric.sync_fact",
    "fabric.rpc_request",
    "fabric.rpc_response",
}


def load_schemas(root: Path) -> dict[str, dict]:
    """Load every ``<topic>.schema.json`` under *root* by their `title:` key."""
    out: dict[str, dict] = {}
    for path in sorted(root.glob("*.schema.json")):
        data = json.loads(path.read_text())
        title = data.get("title") or path.stem.replace(".schema", "")
        out[title] = data
    return out


def validate_schemas(schemas: dict[str, dict]) -> list[str]:
    """Return a list of error strings; empty means all schemas parse and meet
    Draft 7 metaschema."""
    errors: list[str] = []
    if Draft7Validator is None:
        return ["jsonschema package not installed (pip install jsonschema)"]
    for topic, schema in schemas.items():
        try:
            Draft7Validator.check_schema(schema)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{topic}: schema invalid — {exc}")
    return errors


def coverage_errors(schemas: dict[str, dict]) -> list[str]:
    """Return errors when EXPECTED_TOPICS and schemas disagree."""
    errors: list[str] = []
    missing = EXPECTED_TOPICS - schemas.keys()
    extra = schemas.keys() - EXPECTED_TOPICS
    for t in sorted(missing):
        errors.append(f"missing schema for topic: {t}")
    for t in sorted(extra):
        errors.append(f"orphan schema for topic: {t}")
    return errors


def synth_envelope(topic: str, schema: dict) -> dict:
    """Build a minimal envelope that should validate against *schema*."""
    data_schema = schema["properties"]["data"]
    data = _synth_object(data_schema)
    return {
        "v": 1,
        "ts_ms": 42,
        "source": "synthetic",
        "topic": topic,
        "data": data,
    }


def _synth_object(schema: dict) -> dict:
    """Tiny synth helper — only handles the shapes we use in event data."""
    if schema.get("maxProperties") == 0:
        return {}
    out: dict = {}
    props = schema.get("properties", {})
    for k in schema.get("required", []):
        ps = props.get(k, {})
        out[k] = _synth_value(ps)
    return out


def _synth_value(schema: dict):
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    t = schema.get("type")
    if isinstance(t, list):
        t = t[0]
    if t == "string":
        return "x"
    if t == "integer":
        return int(schema.get("minimum", 0))
    if t == "number":
        return float(schema.get("minimum", 0))
    if t == "boolean":
        return True
    if t == "object":
        return _synth_object(schema)
    if t == "array":
        return []
    return None


def roundtrip_envelope(topic: str, schema: dict) -> str | None:
    """Build, validate via JSON Schema, then parse via Python's Envelope.

    Returns an error string on failure, else ``None``.
    """
    if Draft7Validator is None:
        return None
    env = synth_envelope(topic, schema)
    validator = Draft7Validator(schema)
    errors = sorted(validator.iter_errors(env), key=lambda e: e.path)
    if errors:
        return f"{topic}: synthetic envelope fails schema validation: {errors[0].message}"
    # Round-trip via the Python type.
    sys.path.insert(0, str(REPO / "rpi5" / "src"))
    from blazend.events import Envelope

    parsed = Envelope.from_wire(env)
    if parsed.topic != topic:
        return f"{topic}: Envelope.from_wire returned wrong topic {parsed.topic!r}"
    return None


def main() -> int:
    """Validate the schema dir and optionally write placeholder generated files."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--schemas", type=Path, default=REPO / "configs" / "_schema" / "events")
    ap.add_argument("--python-out", type=Path, default=None,
                    help="write the placeholder _generated.py (M2 will replace it)")
    ap.add_argument("--rust-out", type=Path, default=None,
                    help="write the placeholder _generated.rs (M2 will replace it)")
    args = ap.parse_args()

    schemas = load_schemas(args.schemas)
    errors: list[str] = []
    errors.extend(validate_schemas(schemas))
    errors.extend(coverage_errors(schemas))
    for topic, schema in schemas.items():
        if (err := roundtrip_envelope(topic, schema)) is not None:
            errors.append(err)

    if args.python_out is not None:
        _write_placeholder_py(args.python_out, sorted(schemas))
    if args.rust_out is not None:
        _write_placeholder_rs(args.rust_out, sorted(schemas))

    if errors:
        for e in errors:
            print(f"FAIL  {e}", file=sys.stderr)
        return 1
    print(f"OK    {len(schemas)} schemas valid, round-trip OK.")
    return 0


def _write_placeholder_py(path: Path, topics: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    banner = (
        '"""Generated by scripts/gen-event-types.py — DO NOT EDIT.\n\n'
        "M1 placeholder: lists the known topics. M2 will replace this with\n"
        "datamodel-code-generator output from the schema dir.\n"
        '"""\n\n'
    )
    body = (
        "from __future__ import annotations\n\n"
        f"KNOWN_TOPICS = {tuple(topics)!r}\n"
    )
    path.write_text(banner + body)


def _write_placeholder_rs(path: Path, topics: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    quoted = ", ".join(f'"{t}"' for t in topics)
    text = (
        "// Generated by scripts/gen-event-types.py — DO NOT EDIT.\n"
        "//\n"
        "// M1 placeholder: lists known topic strings as a compile-time array.\n"
        "// M2 will replace this with `typify` output from the schema dir.\n\n"
        f"pub const KNOWN_TOPICS: &[&str] = &[{quoted}];\n"
    )
    path.write_text(text)


if __name__ == "__main__":
    sys.exit(main())
