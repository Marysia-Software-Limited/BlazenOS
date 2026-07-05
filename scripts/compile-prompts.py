#!/usr/bin/env python3
"""Compile Jessica's recommendation prompts OFFLINE with DSPy (runs on paul).

The recommendation reasoning is a DSPy signature (``BookQuery → Recommendation``).
This script optimises it — bootstrapping few-shot demos with the LAN Ollama Bielik
11B as teacher — and EXPORTS the result as static JSON under ``configs/prompts/``.
The Pi loads those artifacts via ``assistant/prompts.py`` and fills them at
runtime, so **the device never imports dspy**; DSPy lives only in this offline
toolchain.

An artifact only carries the compiled ``demos`` (rendered to the same
``NUMER:/POLECAM:`` format the runtime parses); the system + instruction stay in
``prompts.py`` defaults and are merged in on load.

Fallback: if dspy (or the Ollama teacher) is unavailable, ``--curated`` — or an
automatic fallback — exports the trainset's gold examples directly as demos, so a
usable static prompt always ships.

Usage (on paul, Ollama Bielik 11B reachable at localhost:11434):
  pip install dspy-ai
  PYTHONPATH=rpi5/src python scripts/compile-prompts.py \
      --trainset scripts/prompt-trainset/books.jsonl \
      --name book_recommendation --out configs/prompts
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_OLLAMA_URL = "http://localhost:11434"
_OLLAMA_MODEL = "ollama_chat/SpeakLeash/bielik-11b-v2.3-instruct:Q8_0"


def load_trainset(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def render_demo(row: dict) -> str:
    """Render one example into the runtime's book-recommendation format."""
    cands = "\n".join(row["candidates"])
    return (
        f"Użytkownik prosi o książkę: „{row['query']}”. Oto dostępne pozycje:\n"
        f"{cands}\nNUMER: {row['gold_number']}\nPOLECAM: {row['gold_pitch']}"
    )


def compile_with_dspy(rows: list[dict]) -> list[str] | None:
    """Bootstrap few-shot demos with DSPy + the Ollama 11B teacher. Returns the
    rendered demo strings, or ``None`` if dspy/teacher is unavailable."""
    try:
        import dspy  # noqa: PLC0415
    except ImportError:
        print("dspy not installed — falling back to curated demos", file=sys.stderr)
        return None
    try:
        lm = dspy.LM(_OLLAMA_MODEL, api_base=_OLLAMA_URL, api_key="", temperature=0.3)
        dspy.configure(lm=lm)

        class BookRecommendation(dspy.Signature):
            """Wybierz JEDEN audiobook z listy, który najlepiej pasuje do prośby. Odpowiedz po polsku."""

            query: str = dspy.InputField(desc="prośba użytkownika")
            candidates: str = dspy.InputField(desc="ponumerowana lista audiobooków")
            number: int = dspy.OutputField(desc="numer wybranej pozycji z listy")
            pitch: str = dspy.OutputField(desc="jedno-dwa zdania po polsku, dlaczego warto")

        def metric(example, pred, trace=None) -> bool:  # noqa: ANN001
            try:
                n = int(pred.number)
            except (TypeError, ValueError):
                return False
            return 1 <= n <= len(example.candidates.splitlines()) and bool(str(pred.pitch).strip())

        trainset = [
            dspy.Example(
                query=r["query"], candidates="\n".join(r["candidates"]),
                number=r["gold_number"], pitch=r["gold_pitch"],
            ).with_inputs("query", "candidates")
            for r in rows
        ]
        from dspy.teleprompt import BootstrapFewShot  # noqa: PLC0415

        opt = BootstrapFewShot(metric=metric, max_bootstrapped_demos=4, max_labeled_demos=4)
        compiled = opt.compile(dspy.Predict(BookRecommendation), trainset=trainset)
        demos = getattr(compiled, "demos", None) or compiled.predictors()[0].demos
        out: list[str] = []
        for d in demos:
            q = getattr(d, "query", "")
            cands = getattr(d, "candidates", "")
            num = getattr(d, "number", "")
            pitch = getattr(d, "pitch", "")
            if q and cands and str(num).strip():
                out.append(
                    f"Użytkownik prosi o książkę: „{q}”. Oto dostępne pozycje:\n"
                    f"{cands}\nNUMER: {num}\nPOLECAM: {pitch}"
                )
        print(f"dspy: bootstrapped {len(out)} demos via {_OLLAMA_MODEL}", file=sys.stderr)
        return out or None
    except Exception as e:  # noqa: BLE001 — any teacher/optimiser failure → curated fallback
        print(f"dspy compile failed ({e}) — falling back to curated demos", file=sys.stderr)
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trainset", type=Path, default=Path("scripts/prompt-trainset/books.jsonl"))
    ap.add_argument("--name", default="book_recommendation")
    ap.add_argument("--out", type=Path, default=Path("configs/prompts"))
    ap.add_argument("--curated", action="store_true", help="skip DSPy; export gold demos directly")
    ap.add_argument("--stamp", default="", help="ISO timestamp to record (offline; no clock here)")
    args = ap.parse_args()

    rows = load_trainset(args.trainset)
    demos = None if args.curated else compile_with_dspy(rows)
    source = "dspy-bootstrap+bielik-11b"
    if demos is None:
        demos = [render_demo(r) for r in rows]
        source = "curated-goldset"

    args.out.mkdir(parents=True, exist_ok=True)
    artifact = {
        "name": args.name,
        "demos": demos,
        "compiled_with": source,
        "trainset": str(args.trainset),
        "compiled_at": args.stamp,
    }
    dest = args.out / f"{args.name}.json"
    dest.write_text(json.dumps(artifact, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"prompt: {len(demos)} demos ({source}) → {dest}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
