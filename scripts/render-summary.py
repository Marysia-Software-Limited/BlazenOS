#!/usr/bin/env python3
"""Nightly: fold the literatura render manifest into a dated fabric note.

Reads the batch-render manifest (``render-literatura.json`` in the shared audiobook
library) and writes a one-line done/failed summary as a note in this node's
``memory.json`` — the same store ``jessica --serve-fabric`` serves — so the
constellation (and the operator over voice) sees how the long-haul render is going
without tailing logs on paul. The note id is stable per day, so re-runs replace it.

    BLAZEN_NODE=paul scripts/render-summary.py [--lang pl] [--data memory.json]

Wire it to a timer: ``linux/systemd/render-summary.{service,timer}``.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from context_sync.paths import context_paths
from jessica_linux import books, render_report


def _load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def upsert_note(memory: dict, note: dict) -> dict:
    """Insert ``note`` (by ``id``) into a ``memory.json`` dict, replacing a same-id
    note. Keeps notes sorted by ``created`` for deterministic fabric merges."""
    notes = [n for n in memory.get("notes", []) if n.get("id") != note["id"]]
    notes.append(note)
    notes.sort(key=lambda n: (str(n.get("created", "")), str(n.get("id"))))
    memory["notes"] = notes
    return memory


def main() -> int:
    ap = argparse.ArgumentParser(description="Write a nightly render summary to the fabric log.")
    ap.add_argument("--lang", default="pl", choices=["pl", "en"], help="note language (default: pl)")
    ap.add_argument("--data", help="memory.json path (default: $BLAZEN_DATA_DIR/memory.json)")
    ap.add_argument("--manifest", help="render manifest path (default: shared library)")
    args = ap.parse_args()

    manifest_path = Path(args.manifest) if args.manifest else books._LIBRARY / "render-literatura.json"
    manifest = _load_json(manifest_path)
    if not manifest:
        print(f"brak manifestu renderu ({manifest_path}) — nic do podsumowania", file=sys.stderr)
        return 0  # nothing rendered yet is not an error

    now = datetime.now()
    note_id, text = render_report.summarize(manifest, now=now, lang=args.lang)

    memory_path, _ = context_paths(args.data)
    memory = _load_json(memory_path)
    upsert_note(memory, {"id": note_id, "text": text, "created": now.isoformat(),
                         "title": "render", "kind": "note_created"})
    _write_json(memory_path, memory)

    print(f"{text}  → {memory_path} ({note_id})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
