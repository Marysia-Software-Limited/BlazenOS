#!/usr/bin/env bash
# scripts/setup-qemu-env.sh — prepare a QEMU bring-up environment by
# downloading the latest Raspberry Pi OS Lite arm64 image and extracting
# the kernel + DTB that `run-vm.sh` needs to boot the image.
#
# This is NOT a blazen_os image build (`make vm-image` does that via
# pi-gen). It just gives us a baseline arm64 image to boot in QEMU and
# verify our toolchain — useful for M1 sanity checking.
#
# Outputs (all under build/vm-boot/):
#   - raspios-lite-arm64.img      (raw, decompressed)
#   - kernel8.img                 (extracted from /boot/firmware/)
#   - bcm2710-rpi-3-b-plus.dtb    (or whichever matches the machine)
#   - boot.img                    (FAT32 partition image — for inspection)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="$REPO_ROOT/build/vm-boot"
BASE_URL="https://downloads.raspberrypi.com/raspios_lite_arm64/images/raspios_lite_arm64-2026-05-15"
IMAGE_XZ="2026-05-15-raspios-bookworm-arm64-lite.img.xz"
IMAGE_RAW="raspios-lite-arm64.img"

log()  { printf '\033[1;34m[setup-qemu]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[setup-qemu]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[setup-qemu]\033[0m %s\n' "$*" >&2; exit 1; }

mkdir -p "$BUILD_DIR"
cd "$BUILD_DIR"

if [ ! -f "$IMAGE_RAW" ]; then
  if [ ! -f "$IMAGE_XZ" ]; then
    log "Downloading $IMAGE_XZ"
    if command -v curl >/dev/null; then
      curl -fL --progress-bar -o "$IMAGE_XZ" "$BASE_URL/$IMAGE_XZ"
    else
      wget --progress=bar:force -O "$IMAGE_XZ" "$BASE_URL/$IMAGE_XZ"
    fi
  fi
  log "Decompressing $IMAGE_XZ"
  command -v xz >/dev/null || die "xz not on PATH; brew install xz or apt install xz-utils"
  xz -d --keep --force "$IMAGE_XZ" -c > "$IMAGE_RAW"
fi

log "Locating boot partition offset"
fdisk_out=$(LC_ALL=C fdisk -l "$IMAGE_RAW" 2>/dev/null || true)
if [ -z "$fdisk_out" ]; then
  # macOS: hdiutil attach + diskutil mount.
  if [[ "$(uname -s)" == "Darwin" ]]; then
    log "macOS path: hdiutil attach"
    attach_out=$(hdiutil attach -imagekey diskimage-class=CRawDiskImage -nomount "$IMAGE_RAW")
    boot_disk=$(echo "$attach_out" | awk '/Windows_FAT_32/ {print $1; exit}' || true)
    if [ -z "$boot_disk" ]; then
      hdiutil detach "$(echo "$attach_out" | awk 'NR==1 {print $1}')" || true
      die "Could not find a FAT32 boot partition in $IMAGE_RAW"
    fi
    mountpoint=$(mktemp -d -t blazen-boot)
    mount -t msdos "$boot_disk" "$mountpoint" 2>/dev/null || \
      mount -t msdos -o nobrowse "$boot_disk" "$mountpoint"
    cp -v "$mountpoint"/kernel8.img ./kernel8.img 2>/dev/null || warn "kernel8.img not in boot partition"
    cp -v "$mountpoint"/bcm2710-rpi-3-b-plus.dtb ./ 2>/dev/null || true
    cp -v "$mountpoint"/bcm2711-rpi-4-b.dtb ./ 2>/dev/null || true
    umount "$mountpoint" || true
    hdiutil detach "${boot_disk%s1}" || hdiutil detach "${boot_disk%s2}" || true
  else
    die "fdisk produced no output and we're not on macOS — install util-linux"
  fi
else
  start_sector=$(echo "$fdisk_out" | awk '/W95 FAT32|c W95/ {print $2; exit}')
  [ -z "$start_sector" ] && start_sector=$(echo "$fdisk_out" | awk '/^.*\.img1 /{print $2; exit}')
  offset=$((start_sector * 512))
  log "Mounting boot partition at offset $offset"
  loopdev=$(sudo losetup --find --show -o "$offset" "$IMAGE_RAW")
  mountpoint=$(mktemp -d)
  sudo mount "$loopdev" "$mountpoint"
  sudo cp -v "$mountpoint"/kernel8.img ./
  sudo cp -v "$mountpoint"/bcm2710-rpi-3-b-plus.dtb ./ 2>/dev/null || true
  sudo cp -v "$mountpoint"/bcm2711-rpi-4-b.dtb ./ 2>/dev/null || true
  sudo umount "$mountpoint"
  sudo losetup -d "$loopdev"
fi

log "Done. Artifacts in $BUILD_DIR:"
ls -lh "$BUILD_DIR"

cat <<EOF

Next steps:
  - Try a sanity boot:    make run-vm   (uses configs/vm/qemu-raspi.yaml)
  - Build the blazen image: make vm-image   (pi-gen — requires Docker)

Note: this only proves QEMU + the upstream image work. It does NOT install
blazend-* yet — that lands in M1 once stage-blazen/ is wired in.
EOF
