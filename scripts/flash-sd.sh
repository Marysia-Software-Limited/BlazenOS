#!/usr/bin/env bash
# scripts/flash-sd.sh — write a blazen_os .img to a real SD card.
# Guarded: requires confirmation; refuses non-removable devices on Linux.

set -euo pipefail

IMAGE=""
DEVICE=""

usage() {
  cat <<USAGE
Usage: $0 --image <path.img> --device </dev/diskN | /dev/sdX>

WARNING: This destroys all data on the target device. Triple-check
before confirming.
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --image)  IMAGE="$2"; shift 2 ;;
    --device) DEVICE="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 1 ;;
  esac
done

[ -z "$IMAGE" ] || [ -z "$DEVICE" ] && { usage; exit 1; }
[ -f "$IMAGE" ] || { echo "image not found: $IMAGE"; exit 1; }
[ -e "$DEVICE" ] || { echo "device not found: $DEVICE"; exit 1; }

OS=$(uname -s)
case "$OS" in
  Linux)
    if [ "$(lsblk -dno RM "$DEVICE")" != "1" ]; then
      echo "REFUSED: $DEVICE is not removable. Aborting for safety."
      exit 1
    fi
    ;;
  Darwin)
    if ! diskutil info "$DEVICE" | grep -qi 'Removable Media: *(Yes|Removable)'; then
      echo "WARN: cannot confirm $DEVICE is removable on macOS; double-check."
    fi
    ;;
esac

echo
echo "About to write $IMAGE  ->  $DEVICE"
echo "ALL data on $DEVICE will be permanently destroyed."
read -p "Type the device path exactly to confirm: " confirm
if [ "$confirm" != "$DEVICE" ]; then
  echo "Mismatch; aborting."
  exit 1
fi

case "$OS" in
  Linux)
    sudo dd if="$IMAGE" of="$DEVICE" bs=4M status=progress conv=fsync
    sync
    ;;
  Darwin)
    diskutil unmountDisk "$DEVICE" || true
    sudo dd if="$IMAGE" of="${DEVICE/disk/rdisk}" bs=4m
    diskutil eject "$DEVICE" || true
    ;;
esac

echo "Done. Eject the card, insert into the Pi, and power on."
