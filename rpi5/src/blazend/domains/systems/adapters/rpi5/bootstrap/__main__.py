"""First-boot bootstrap. Reads /boot/blazen-firstboot/, lays down /etc/blazen/.

SSH is on by default in the image (pubkey-only); the firstboot
`authorized_keys` is the operator key destined for ~blazen/.ssh/authorized_keys
so the device becomes reachable. The bootstrap does NOT disable SSH afterwards.

M1 skeleton: logs what it would do. Real logic in M1 hardware bring-up.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path

log = logging.getLogger("blazend.domains.systems.adapters.rpi5.bootstrap")

BOOT_DIR = Path("/boot/blazen-firstboot")
ETC_DIR = Path("/etc/blazen")


def main() -> int:
    """Lay down /etc/blazen/ from /boot/blazen-firstboot/."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    parser = argparse.ArgumentParser(prog="blazend-bootstrap")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--boot-dir", type=Path, default=BOOT_DIR)
    parser.add_argument("--etc-dir", type=Path, default=ETC_DIR)
    args = parser.parse_args()

    if not args.boot_dir.is_dir():
        log.info("no firstboot dir at %s; nothing to do", args.boot_dir)
        return 0

    log.info("would seed %s from %s (dry_run=%s)", args.etc_dir, args.boot_dir, args.dry_run)
    if args.dry_run:
        return 0

    args.etc_dir.mkdir(parents=True, exist_ok=True)
    for name in ("blazen-bootstrap.yaml", "wpa_supplicant.conf", "authorized_keys"):
        src = args.boot_dir / name
        if src.is_file():
            shutil.copy2(src, args.etc_dir / name)
            log.info("copied %s -> %s", src, args.etc_dir / name)

    # Self-disable: remove firstboot dir so SD-yank attacks can't replay it.
    for child in args.boot_dir.iterdir():
        try:
            child.unlink()
        except IsADirectoryError:
            shutil.rmtree(child)
    args.boot_dir.rmdir()
    log.info("firstboot complete; pairing code printed via TTS will follow")
    return 0


if __name__ == "__main__":
    sys.exit(main())
