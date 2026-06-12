#!/usr/bin/env bash
# scripts/dev-run.sh — launch the full blazend stack on the dev host.
# No VM, no Pi. Fastest iteration loop for the maintainer.
#
# Brings up:
#   blazend-audio-in   (Rust, --mock)
#   blazend-audio-out  (Rust, --mock)
#   blazend-wake       (Rust, --mock, fires every 30s)
#   blazend-tts        (Rust, --mock)
#   blazend-health     (Rust)              writes state.json every 5s
#   blazend-asr        (Python, --mock)    fires synthetic asr.final
#   blazend-brain      (Python, --mock)    canned EN/PL replies
#   blazend-orchestrator (Python)          subscribes to all of the above
#
# Logs go to vm-runs/dev-<timestamp>/<unit>.log; pids in /tmp/blazen-<uid>/.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$REPO_ROOT/.venv"
PY="$VENV/bin/python"
# Appliance Rust units live in the rpi5/ project workspace; the shared core
# (blazend-fabric etc.) builds under crates/. dev-run launches appliance units.
CARGO_TARGET="$REPO_ROOT/rpi5/crates/target/debug"
RUN_DIR="$REPO_ROOT/vm-runs/dev-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$RUN_DIR"

# configs/ stays at the repo root (shared contract); the appliance Python
# package lives under rpi5/src.
export BLAZEN_CONFIG_ROOT="$REPO_ROOT/configs"
export PYTHONPATH="$REPO_ROOT/rpi5/src${PYTHONPATH:+:$PYTHONPATH}"
export BLAZEN_RUNTIME_DIR="${BLAZEN_RUNTIME_DIR:-/tmp/blazen-$UID}"
mkdir -p "$BLAZEN_RUNTIME_DIR"

log()  { printf '\033[1;34m[dev-run]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[dev-run]\033[0m %s\n' "$*" >&2; exit 1; }

[ -x "$PY" ] || die "Python venv missing — run 'make python' first"

# Clean up sockets + pid files from any prior run.
rm -f "$BLAZEN_RUNTIME_DIR"/*.sock 2>/dev/null || true

declare -a PIDS=()

launch_rust() {
  local unit="$1"
  shift
  local binary="$CARGO_TARGET/$unit"
  if [ ! -x "$binary" ]; then
    log "missing $binary; building appliance Rust workspace"
    (cd "$REPO_ROOT/rpi5/crates" && cargo build --workspace --quiet)
  fi
  local logf="$RUN_DIR/$unit.log"
  log "starting $unit -> $logf"
  ("$binary" "$@" >>"$logf" 2>&1) &
  PIDS+=($!)
}

launch_py() {
  local mod="$1"
  shift
  local logf="$RUN_DIR/${mod//./_}.log"
  log "starting python -m $mod -> $logf"
  ("$PY" -m "$mod" "$@" >>"$logf" 2>&1) &
  PIDS+=($!)
}

cleanup() {
  log "shutting down (pids: ${PIDS[*]:-none})"
  for pid in "${PIDS[@]}"; do
    kill -TERM "$pid" 2>/dev/null || true
  done
  sleep 0.3
  for pid in "${PIDS[@]}"; do
    kill -KILL "$pid" 2>/dev/null || true
  done
}
trap cleanup EXIT INT TERM

launch_rust blazend-audio-in   --mock
launch_rust blazend-audio-out  --mock
launch_rust blazend-wake       --mock --mock-period-s 15
launch_rust blazend-tts        --mock
launch_rust blazend-health
launch_py   blazend.asr        --mock
launch_rust blazend-nlu                              # routes asr.final → nlu.intent via jessica-core
launch_py   blazend.brain      --mock

# Orchestrator last — it connects to peers.
sleep 1
launch_py   blazend.orchestrator

log "stack up. Live state file:  $BLAZEN_RUNTIME_DIR/state.json"
log "Tail a unit:                 tail -F $RUN_DIR/<unit>.log"
log "Press Ctrl-C to stop."

# Wait for any one to exit; trigger cleanup.
wait -n "${PIDS[@]}" 2>/dev/null || true
log "a unit exited — cleaning up the rest"
