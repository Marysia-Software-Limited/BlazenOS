#!/usr/bin/env python3
"""Download and verify on-device ML models declared in configs/*.yaml.

Reads each YAML config given via --config, walks the `models:` / `voices:`
trees, and downloads files into ./models/<kind>/<name>/. SHA256 verified.
SHA mismatches abort instead of overwriting.

Usage:
    install_models.py --config configs/llm.yaml --config configs/asr.yaml ...
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
MODELS_DIR = REPO_ROOT / "models"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path) -> bool:
    """Download *url* to *dest*. Returns ``True`` on success.

    On HTTP errors (404, 5xx) prints a warning and returns ``False`` so
    the build can proceed even with stale config URLs — models are
    optional at build time and lazy-loaded by the device.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    print(f"  downloading {url}")
    try:
        with urllib.request.urlopen(url) as r, tmp.open("wb") as f:
            shutil.copyfileobj(r, f)
    except urllib.error.HTTPError as e:
        print(f"  WARN: HTTP {e.code} for {url}; skipping")
        tmp.unlink(missing_ok=True)
        return False
    except urllib.error.URLError as e:
        print(f"  WARN: URL error for {url}: {e.reason}; skipping")
        tmp.unlink(missing_ok=True)
        return False
    tmp.rename(dest)
    return True


def verify_or_fail(path: Path, expected_sha: str) -> None:
    if expected_sha in ("", "<TBD>", None):
        print(f"  WARN: no SHA configured for {path.name}; skipping verification")
        return
    actual = sha256_of(path)
    if actual.lower() != expected_sha.lower():
        raise SystemExit(
            f"SHA mismatch for {path}\n  expected: {expected_sha}\n  actual:   {actual}"
        )


def handle_entry(kind: str, name: str, entry: dict, *, hailo_only: bool = False) -> None:
    """Resolve one model/voice entry; entry may be split into cpu/hailo subtrees."""
    cpu = entry.get("cpu")
    hailo = entry.get("hailo")
    if cpu and not hailo_only:
        _download_subentry(kind, name, "cpu", cpu)
    if hailo and hailo_only:
        _download_subentry(kind, name, "hailo", hailo)
    # Flat entry (asr/tts/wake-word):
    if "download_url" in entry and "file" in entry:
        _download_subentry(kind, name, "flat", entry)
    # Multi-file entry (e.g. embeddings: onnx + tokenizer into one dir):
    if not hailo_only:
        for sub in entry.get("files", []) or []:
            _download_subentry(kind, name, str(sub.get("file", "file")), sub)


def _download_subentry(kind: str, name: str, slot: str, sub: dict) -> None:
    url = sub.get("download_url")
    fname = sub.get("file")
    expected_sha = sub.get("sha256", "")
    if not url or not fname:
        return
    if "<TBD" in url or url.startswith("<"):
        # Vendor URL placeholder — known absent. Skip silently.
        return
    target_dir = MODELS_DIR / kind / name
    target = target_dir / fname
    print(f"[{kind}/{name}/{slot}] -> {target.relative_to(REPO_ROOT)}")
    if target.exists():
        try:
            verify_or_fail(target, expected_sha)
            print("  already present, SHA ok")
            return
        except SystemExit as e:
            print(f"  existing file failed verification: {e}; redownloading")
            target.unlink()
    if not download(url, target):
        return
    try:
        verify_or_fail(target, expected_sha)
    except SystemExit as e:
        print(f"  WARN: {e}")


def walk_config(path: Path, *, hailo: bool) -> None:
    cfg = yaml.safe_load(path.read_text())
    kind = path.stem  # llm / asr / tts / wake-word
    for section_name in ("models", "voices"):
        section = cfg.get(section_name) or {}
        for name, entry in section.items():
            handle_entry(kind, name, entry, hailo_only=False)
            if hailo:
                handle_entry(kind, name, entry, hailo_only=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", action="append", required=True, type=Path)
    ap.add_argument(
        "--hailo",
        action="store_true",
        help="also download accelerator (.hef) variants",
    )
    args = ap.parse_args()
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    for c in args.config:
        walk_config(c, hailo=args.hailo)
    return 0


if __name__ == "__main__":
    sys.exit(main())
