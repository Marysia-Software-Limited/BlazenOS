#!/usr/bin/env python3
"""tests/tools/e2e-runner.py — driver for Tier 2 + 3 voice scenarios.

The runner:
  1. Boots (or attaches to) a QEMU VM with the blazen_os image.
  2. Plays synthesised WAVs into the VM's virtual mic per scenario turn.
  3. Captures the VM's virtual speaker output.
  4. Transcribes the captured audio via host-side faster-whisper.
  5. Asserts on scenario `expect` clauses (transcript, intent, state, latency).
  6. Reports per-scenario pass/fail with timings.

This is the skeleton — it parses scenarios and walks the structure but
does NOT execute against a real VM yet (that lands in M2 / M3 as the VM
image becomes bootable).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

APP_ROOT = Path(__file__).resolve().parents[2]   # rpi5/ (appliance project)
REPO = Path(__file__).resolve().parents[3]        # repo root (shared artefacts)
SCENARIOS_DIR = APP_ROOT / "tests" / "scenarios"
RUNS_DIR = REPO / "vm-runs"


@dataclass
class TurnResult:
    index: int
    label: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    timings_ms: dict[str, float] = field(default_factory=dict)


@dataclass
class ScenarioResult:
    id: str
    description: str
    passed: bool
    turns: list[TurnResult]
    notes: list[str] = field(default_factory=list)


class VMHandle:
    """Stub VM driver. Replace with real QEMU + SSH + virtual-audio bridge."""

    def __init__(self, image: Path):
        self.image = image
        self.state: dict[str, Any] = {}

    def start(self) -> None:
        # TODO(M1): boot QEMU, wait for SSH on 2222, verify systemd ready.
        pass

    def stop(self) -> None:
        pass

    def play_user_audio(self, wav_path: Path) -> None:
        # TODO(M2): stream wav into virtual mic via PipeWire/PortAudio file.
        pass

    def capture_assistant_audio(self, timeout_ms: int) -> Path:
        # TODO(M2): capture virtual speaker; return path to WAV.
        return Path("/dev/null")

    def transcribe(self, wav_path: Path) -> str:
        # TODO(M3): faster-whisper on host.
        return ""

    def inject(self, kind: str, **kwargs) -> None:
        # TODO(M5): for fault-injection scenarios via SSH.
        pass

    def get_state(self, key: str) -> Any:
        return self.state.get(key)

    def set_preconditions(self, pre: dict[str, Any]) -> None:
        for k, v in (pre or {}).items():
            self.state[k] = v


def synth_user_turns(scenario: dict, out_dir: Path) -> None:
    """For each `user:` turn, ensure a synthesised WAV exists under fixtures.

    Calls tests/tools/synth-audio.py at build time; here we only check
    paths exist. The actual synthesis is owned by `make audio-fixtures`.
    """
    for i, turn in enumerate(scenario.get("turns", [])):
        if "user" in turn:
            (out_dir / f"turn_{i:02d}_user.wav").touch(exist_ok=True)


def evaluate_expect(expect: Any, vm: VMHandle, transcript: str) -> list[str]:
    """Return a list of failure messages (empty == pass)."""
    failures: list[str] = []
    if expect is None:
        return failures
    if isinstance(expect, str):
        # shorthand like `expect: wake_acknowledged`
        if expect == "wake_acknowledged" and not vm.get_state("wake_word.last_fired"):
            failures.append("wake not acknowledged")
        return failures

    if not isinstance(expect, dict):
        failures.append(f"unknown expect form: {expect!r}")
        return failures

    if expect.get("wake_acknowledged") and not vm.get_state("wake_word.last_fired"):
        failures.append("wake not acknowledged")

    if "assistant_says_contains" in expect:
        needle = expect["assistant_says_contains"].lower()
        if needle not in (transcript or "").lower():
            failures.append(f"transcript missing phrase: {needle!r}")

    if "assistant_says_any" in expect:
        if not any(s.lower() in (transcript or "").lower() for s in expect["assistant_says_any"]):
            failures.append("none of assistant_says_any matched")

    if "assistant_intent_matches" in expect:
        # TODO(M5): query VM-side classified intent
        pass

    if "assistant_semantic_similarity" in expect:
        # TODO(M4): host-side embedding model
        pass

    if "state" in expect:
        for k, v in expect["state"].items():
            actual = vm.get_state(k)
            if actual != v:
                failures.append(f"state.{k}: expected {v!r}, got {actual!r}")

    return failures


def run_scenario(path: Path, vm: VMHandle) -> ScenarioResult:
    scenario = yaml.safe_load(path.read_text())
    fixtures_dir = APP_ROOT / "tests" / "fixtures" / "audio" / scenario["id"]
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    synth_user_turns(scenario, fixtures_dir)

    vm.start()
    vm.set_preconditions(scenario.get("preconditions") or {})
    results: list[TurnResult] = []
    try:
        for i, turn in enumerate(scenario.get("turns", [])):
            t0 = time.perf_counter()
            label = turn.get("user") or turn.get("inject") or f"turn_{i}"
            transcript = ""

            if "user" in turn:
                vm.play_user_audio(fixtures_dir / f"turn_{i:02d}_user.wav")
                wav = vm.capture_assistant_audio(timeout_ms=5000)
                transcript = vm.transcribe(wav)
            elif "inject" in turn:
                vm.inject(turn["inject"], **{k: v for k, v in turn.items() if k != "inject"})

            failures = evaluate_expect(turn.get("expect"), vm, transcript)
            elapsed = (time.perf_counter() - t0) * 1000
            results.append(
                TurnResult(
                    index=i,
                    label=str(label)[:80],
                    passed=not failures,
                    failures=failures,
                    timings_ms={"turn_total": elapsed},
                )
            )
    finally:
        vm.stop()

    return ScenarioResult(
        id=scenario["id"],
        description=scenario.get("description", ""),
        passed=all(r.passed for r in results),
        turns=results,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="run every scenario")
    ap.add_argument("--scenario", type=Path, help="single scenario YAML")
    ap.add_argument("--image", type=Path, default=REPO / "vm-images" / "blazen_os-0.0.1-dev.qcow2")
    ap.add_argument("--soak", type=str, help="soak duration, e.g. 24h")
    ap.add_argument("--json", action="store_true", help="emit JSON result")
    args = ap.parse_args()

    if args.soak:
        print(f"TODO: soak runner not implemented (requested {args.soak})")
        return 0

    if args.all:
        targets = sorted(SCENARIOS_DIR.glob("*.yaml"))
    elif args.scenario:
        targets = [args.scenario]
    else:
        ap.error("use --all or --scenario")
        return 2

    vm = VMHandle(args.image)
    summary = []
    overall = True
    for p in targets:
        res = run_scenario(p, vm)
        overall = overall and res.passed
        summary.append(res)
        line = "PASS" if res.passed else "FAIL"
        print(f"[{line}] {res.id}  ({sum(1 for t in res.turns if t.passed)}/{len(res.turns)} turns)")
        for t in res.turns:
            if not t.passed:
                for f in t.failures:
                    print(f"    turn {t.index} ({t.label}): {f}")

    if args.json:
        print(json.dumps([{
            "id": r.id, "passed": r.passed,
            "turns": [{"i": t.index, "label": t.label, "passed": t.passed, "failures": t.failures} for t in r.turns],
        } for r in summary], indent=2))

    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
