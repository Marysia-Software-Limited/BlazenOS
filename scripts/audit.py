#!/usr/bin/env python3
"""scripts/audit.py — lint configs + dry-run firewall + report missing SHAs.

Cheap checks that should always be green on `main`. Run by `make audit`.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
CFG = REPO / "configs"


def check_yaml_loads() -> list[str]:
    errors = []
    for p in CFG.rglob("*.yaml"):
        try:
            yaml.safe_load(p.read_text())
        except yaml.YAMLError as e:
            errors.append(f"YAML parse error in {p.relative_to(REPO)}: {e}")
    return errors


def check_model_shas() -> list[str]:
    warnings = []
    for p in (CFG / "asr.yaml", CFG / "tts.yaml", CFG / "llm.yaml", CFG / "wake-word.yaml"):
        if not p.exists():
            continue
        data = yaml.safe_load(p.read_text())
        for section in ("models", "voices"):
            for name, entry in (data.get(section) or {}).items():
                _walk_sha_entry(p, section, name, entry, warnings)
    return warnings


def _walk_sha_entry(p, section, name, entry, warnings):
    if "sha256" in entry and entry.get("download_url") and entry.get("sha256") in ("", "<TBD>", None):
        warnings.append(f"{p.name}:{section}/{name} sha256 = <TBD>")
    for slot in ("cpu", "hailo"):
        sub = entry.get(slot)
        if isinstance(sub, dict) and sub.get("download_url") and sub.get("sha256") in ("", "<TBD>", None):
            warnings.append(f"{p.name}:{section}/{name}/{slot} sha256 = <TBD>")


def check_voice_policy() -> list[str]:
    errors = []
    pol = yaml.safe_load((CFG / "voice-policy.yaml").read_text())
    allow = pol.get("allow_voice_mutation", {})
    for k, v in allow.items():
        confirm = (v or {}).get("confirm")
        if confirm not in ("never", "single", "loud", "double_loud", "timed_window"):
            errors.append(f"voice-policy.yaml: bad confirm '{confirm}' for {k}")
    return errors


def check_secrets_not_committed() -> list[str]:
    """Heuristic: scan committed YAML for obvious secret-looking values."""
    bad = []
    pat = re.compile(r"(?i)(secret|password|psk)\s*:\s*\S+")
    for p in CFG.rglob("*.yaml"):
        for n, line in enumerate(p.read_text().splitlines(), 1):
            if pat.search(line) and "<TBD>" not in line and "BLAZEN_" not in line:
                bad.append(f"{p.relative_to(REPO)}:{n}: possible secret literal")
    return bad


def main() -> int:
    yaml_errs = check_yaml_loads()
    sha_warns = check_model_shas()
    pol_errs = check_voice_policy()
    sec_warns = check_secrets_not_committed()

    out = {
        "yaml_errors": yaml_errs,
        "model_sha_tbd": sha_warns,
        "voice_policy_errors": pol_errs,
        "secret_smell": sec_warns,
    }
    print(json.dumps(out, indent=2))
    return 1 if yaml_errs or pol_errs or sec_warns else 0


if __name__ == "__main__":
    sys.exit(main())
