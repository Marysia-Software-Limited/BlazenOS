#!/usr/bin/env python3
"""tests/tools/synth-audio.py — render every `user:` line in scenario YAMLs
to a 16 kHz mono WAV via Piper. Run by `make audio-fixtures`.

Idempotent: skips files already present unless --force is set. Picks the
Piper voice per turn from (in order):
  1. turn-level `synth_voice:` (overrides everything)
  2. scenario-level `synth_voice:`
  3. language-default map (en -> amy, pl -> gosia)
  4. CLI --voice override

Polish turns get a Polish voice so wake/ASR see realistic phonetics
rather than Anglo-Polish.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

DEFAULT_VOICE_BY_LANGUAGE = {
    "en": "en_US-amy-medium",      # different from assistant default
    "pl": "pl_PL-gosia-medium",    # different from assistant default (darkman)
}
FALLBACK_VOICE = "en_US-amy-medium"


def synth(text: str, out_wav: Path, voice_path: Path, force: bool) -> None:
    if out_wav.exists() and not force:
        return
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    piper = shutil.which("piper")
    if not piper:
        sys.exit("piper not on PATH; install or run inside the venv with piper-tts")
    proc = subprocess.run(
        [piper, "--model", str(voice_path), "--output_file", str(out_wav)],
        input=text.encode("utf-8"),
        check=True,
    )
    _ = proc  # silence linters
    # Re-encode to 16k mono if needed (Piper renders at 22.05k).
    sox = shutil.which("sox") or shutil.which("ffmpeg")
    if sox and sox.endswith("ffmpeg"):
        tmp = out_wav.with_suffix(".16k.wav")
        subprocess.run(
            [sox, "-y", "-i", str(out_wav), "-ar", "16000", "-ac", "1", str(tmp)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        tmp.replace(out_wav)


def _voice_for(scenario: dict, turn: dict, cli_voice: Path | None) -> str:
    if cli_voice:
        return cli_voice.stem
    if turn.get("synth_voice"):
        return turn["synth_voice"]
    if scenario.get("synth_voice"):
        return scenario["synth_voice"]
    lang = scenario.get("language", "en")
    return DEFAULT_VOICE_BY_LANGUAGE.get(lang, FALLBACK_VOICE)


def _resolve_voice_path(voice_name: str) -> Path:
    return Path(__file__).resolve().parents[3] / "models" / "tts" / voice_name / f"{voice_name}.onnx"


def walk(scenarios_dir: Path, out_root: Path, cli_voice: Path | None, force: bool) -> None:
    for p in sorted(scenarios_dir.glob("*.yaml")):
        scenario = yaml.safe_load(p.read_text())
        sid = scenario["id"]
        out_dir = out_root / sid
        for i, turn in enumerate(scenario.get("turns", [])):
            text = turn.get("user")
            if not text:
                continue
            voice_name = _voice_for(scenario, turn, cli_voice)
            voice_path = _resolve_voice_path(voice_name)
            if not voice_path.exists():
                print(f"  WARN: voice {voice_name} not on disk ({voice_path}); run `make models`. skipping turn {i}.")
                continue
            target = out_dir / f"turn_{i:02d}_user.wav"
            synth(text, target, voice_path, force)
            print(f"  rendered {target.relative_to(out_root.parent.parent)}  [{voice_name}]")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenarios", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--voice", type=Path,
                    help="path to a Piper .onnx voice file (overrides per-scenario / per-language picking)")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    walk(args.scenarios, args.out, args.voice, args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
