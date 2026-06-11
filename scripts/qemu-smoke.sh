#!/usr/bin/env bash
# scripts/qemu-smoke.sh — verify qemu-system-aarch64 is installed and
# can start a `virt` machine on this host. Does NOT boot a real OS;
# only proves the toolchain can spin up an aarch64 VM at all. This is
# the cheapest gate before attempting the full Pi OS image flow.
#
# Reports:
#   - qemu version
#   - whether kvm/hvf accel is available
#   - whether a minimal VM boots and a few seconds of cpu execution happen

set -euo pipefail

log()  { printf '\033[1;34m[qemu-smoke]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[qemu-smoke]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[qemu-smoke]\033[0m %s\n' "$*" >&2; exit 1; }

# 1. Binary present.
command -v qemu-system-aarch64 >/dev/null \
  || die "qemu-system-aarch64 not on PATH (brew install qemu / apt install qemu-system-arm)"

VERSION=$(qemu-system-aarch64 --version | head -1)
log "found: $VERSION"

# 2. Acceleration probe.
ACCEL="tcg"
case "$(uname -s)" in
  Darwin)
    if qemu-system-aarch64 -accel help 2>&1 | grep -q hvf; then
      ACCEL="hvf"
    fi
    ;;
  Linux)
    if [ -e /dev/kvm ] && [ -r /dev/kvm ]; then
      ACCEL="kvm"
    fi
    ;;
esac
log "using accel: $ACCEL"

# 3. List machines and confirm 'virt' is present.
if qemu-system-aarch64 -machine help 2>&1 | grep -qE '^virt'; then
  log "machine 'virt' available"
else
  warn "machine 'virt' not listed — older qemu? trying anyway"
fi

# Pick a CPU model that's valid for this accel.
case "$ACCEL" in
  hvf|kvm) CPU="host" ;;
  *)       CPU="max"  ;;   # TCG fallback — emulates a generic v8-A
esac
log "using cpu: $CPU"

# 4. Try a tiny boot: -machine virt + no kernel = enters firmware menu
# and exits in ~2s. We just verify QEMU starts without errors.
log "attempting 2-second VM start (no kernel)"
set +e
timeout 4 qemu-system-aarch64 \
  -machine virt \
  -cpu "$CPU" \
  -m 256 \
  -nographic \
  -accel "$ACCEL" \
  -no-reboot \
  >/tmp/qemu-smoke-$$.log 2>&1 &
QPID=$!
sleep 2
if kill -0 "$QPID" 2>/dev/null; then
  kill -INT "$QPID" 2>/dev/null
  sleep 1
  kill -KILL "$QPID" 2>/dev/null
  log "VM started cleanly (killed after 2s)"
  rm -f /tmp/qemu-smoke-$$.log
  exit 0
else
  wait "$QPID" 2>/dev/null
  rc=$?
  if [ "$rc" -eq 124 ]; then
    log "VM was still running at the 4s timeout (good — QEMU is alive)"
    rm -f /tmp/qemu-smoke-$$.log
    exit 0
  fi
  cat /tmp/qemu-smoke-$$.log >&2
  rm -f /tmp/qemu-smoke-$$.log
  die "QEMU exited unexpectedly (rc=$rc)"
fi
