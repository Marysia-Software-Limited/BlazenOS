#!/usr/bin/env bash
# scripts/run-vm.sh — boot the blazen_os SD image in QEMU with virtual audio.
#
# Reads configs/vm/qemu-raspi.yaml for machine type, kernel, dtb, audio
# backend choice, and ssh port-forward. See docs/09-VM-TESTING.md.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
QEMU_CFG="$REPO_ROOT/configs/vm/qemu-raspi.yaml"
IMAGE=""
SERIAL_LOG=""

log() { printf '\033[1;34m[run-vm]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[run-vm] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<USAGE
Usage: $0 --image <path.qcow2> [--config <yaml>] [--serial-log <path>]

Boots the blazen_os image in QEMU. Reads --config (default
configs/vm/qemu-raspi.yaml) for machine + audio settings.
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --image) IMAGE="$2"; shift 2 ;;
    --config) QEMU_CFG="$2"; shift 2 ;;
    --serial-log) SERIAL_LOG="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 1 ;;
  esac
done

[ -z "$IMAGE" ] && die "--image is required"
[ -f "$IMAGE" ] || die "image not found: $IMAGE"
[ -f "$QEMU_CFG" ] || die "config not found: $QEMU_CFG"

# Minimal YAML reader. For sustainability we delegate to Python.
PY="${REPO_ROOT}/.venv/bin/python"
[ -x "$PY" ] || PY=python3

read_cfg() {
  "$PY" - "$QEMU_CFG" "$1" <<'PY'
import sys, yaml
cfg = yaml.safe_load(open(sys.argv[1]))
keys = sys.argv[2].split(".")
v = cfg
for k in keys:
    v = v.get(k)
    if v is None:
        print(""); sys.exit(0)
print(v)
PY
}

MACHINE=$(read_cfg machine)
CPUS=$(read_cfg cpus)
RAM=$(read_cfg ram_mb)
KERNEL=$(read_cfg kernel)
DTB=$(read_cfg dtb)
CMDLINE=$(read_cfg cmdline)
HOST_SSH_PORT=$(read_cfg network.ssh_port_forward.host_port)
AUDIO_BACKEND=$(read_cfg audio_backend.type)

RUN_DIR="$REPO_ROOT/vm-runs/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$RUN_DIR"
[ -z "$SERIAL_LOG" ] && SERIAL_LOG="$RUN_DIR/serial.log"

# Map audio backend.
case "$AUDIO_BACKEND" in
  pipewire) AUDIODEV='-audiodev pipewire,id=ad0' ;;
  portaudio-file) AUDIODEV='-audiodev wav,id=ad0,path=/tmp/blazen-vm-out.wav' ;;
  *) AUDIODEV='-audiodev none,id=ad0' ;;
esac

DEV_KEY="$REPO_ROOT/build/dev-ssh/id_ed25519"
log "Booting $MACHINE  cpus=$CPUS ram=${RAM}MB"
log "Serial log: $SERIAL_LOG"
if [ -f "$DEV_KEY" ]; then
  log "SSH:        ssh -i $DEV_KEY -p ${HOST_SSH_PORT} blazen@localhost   (dev image; pw 'blazen' on serial console)"
else
  log "SSH:        ssh -p ${HOST_SSH_PORT} blazen@localhost   (dev image; pw 'blazen' on serial console)"
fi

exec qemu-system-aarch64 \
  -M "$MACHINE" \
  -smp "$CPUS" \
  -m "${RAM}M" \
  -kernel "$KERNEL" \
  -dtb "$DTB" \
  -drive "file=${IMAGE},format=qcow2,if=sd" \
  -append "$CMDLINE" \
  -netdev "user,id=net0,hostfwd=tcp::${HOST_SSH_PORT}-:22" \
  -device usb-net,netdev=net0 \
  ${AUDIODEV} \
  -device intel-hda \
  -device hda-output,audiodev=ad0 \
  -device hda-micro,audiodev=ad0 \
  -display none \
  -serial "file:${SERIAL_LOG}" \
  -monitor "telnet:127.0.0.1:4444,server,nowait"
