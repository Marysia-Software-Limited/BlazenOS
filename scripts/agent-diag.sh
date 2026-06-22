#!/usr/bin/env bash
# scripts/agent-diag.sh — run the Jessica agent diagnostics harness.
#
# Verbose, per-stage logs for every aspect of an interaction (wake, events,
# ASR, intent, RAG retrieval, LLM, TTS, errors). Wraps
# rpi5/tests/tools/agent_diag.py with the same env voice-run.sh sets up.
#
#   ./scripts/agent-diag.sh "jaka jest pogoda"   # one typed command
#   ./scripts/agent-diag.sh --repl               # interactive loop
#   ./scripts/agent-diag.sh --wav clip.wav       # feed audio through ASR
#   ./scripts/agent-diag.sh --live               # live mic (boots the Rust units)
#
# Pass-through flags: --no-speak, --no-llm, --data-dir DIR.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
[ -f .env ] && { set -a; . ./.env; set +a; }

VENV="$REPO_ROOT/.venv"
PY="$VENV/bin/python"
CARGO_TARGET="$REPO_ROOT/rpi5/crates/target/debug"

export BLAZEN_CONFIG_ROOT="$REPO_ROOT/configs"
export PYTHONPATH="$REPO_ROOT/rpi5/src:$REPO_ROOT/rpi5/tests/tools${PYTHONPATH:+:$PYTHONPATH}"
export BLAZEN_RUNTIME_DIR="${BLAZEN_RUNTIME_DIR:-/tmp/blazen-$UID}"
export BLAZEN_DATA_DIR="${BLAZEN_DATA_DIR:-$REPO_ROOT/vm-runs/jessica-data}"
export BLAZEN_ASR_MODEL="${BLAZEN_ASR_MODEL:-medium}"
export PTT_OUT="${PTT_OUT:-plughw:CARD=wm8960soundcard,DEV=0}"
export BLAZEN_PIPER="${BLAZEN_PIPER:-$VENV/bin/piper}"
export BLAZEN_PLAYER="${BLAZEN_PLAYER:-$CARGO_TARGET/blazend-player}"
export ORT_DYLIB_PATH="${ORT_DYLIB_PATH:-$(find "$VENV" -name 'libonnxruntime.so.1*' 2>/dev/null | head -1)}"
mkdir -p "$BLAZEN_RUNTIME_DIR"

log() { printf '\033[1;34m[agent-diag]\033[0m %s\n' "$*"; }
[ -x "$PY" ] || { echo "[agent-diag] venv missing — run 'make python'"; exit 1; }

# --live needs the mic ring + wake socket; boot the two Rust units like voice-run.
WANT_LIVE=0
for a in "$@"; do [ "$a" = "--live" ] && WANT_LIVE=1; done

declare -a PIDS=()
PW_WAS_UP=0
cleanup() {
  for pid in "${PIDS[@]:-}"; do kill -TERM "$pid" 2>/dev/null || true; done
  if [ "$PW_WAS_UP" = "1" ]; then
    systemctl --user start pipewire.socket pipewire.service wireplumber.service 2>/dev/null || true
  fi
}

if [ "$WANT_LIVE" = "1" ]; then
  trap cleanup EXIT INT TERM
  if command -v systemctl >/dev/null && systemctl --user is-active --quiet pipewire 2>/dev/null; then
    PW_WAS_UP=1
    log "suspending PipeWire (frees the wm8960 for cpal capture)"
    systemctl --user stop wireplumber.service pipewire.service pipewire.socket 2>/dev/null || true
    sleep 1
  fi
  if [ ! -x "$CARGO_TARGET/blazend-audio-in" ] || [ ! -x "$CARGO_TARGET/blazend-wake" ] || [ ! -x "$CARGO_TARGET/blazend-player" ]; then
    log "building appliance Rust workspace…"
    (cd "$REPO_ROOT/rpi5/crates" && cargo build --workspace --quiet)
  fi
  rm -f "$BLAZEN_RUNTIME_DIR"/*.sock 2>/dev/null || true
  log "starting blazend-audio-in + blazend-wake"
  "$CARGO_TARGET/blazend-audio-in" --device "${BLAZEN_AUDIO_DEVICE:-wm8960}" \
    >>"$BLAZEN_RUNTIME_DIR/diag-audio-in.log" 2>&1 & PIDS+=($!)
  "$CARGO_TARGET/blazend-wake" >>"$BLAZEN_RUNTIME_DIR/diag-wake.log" 2>&1 & PIDS+=($!)
  sleep 1.5
fi

exec "$PY" -m agent_diag "$@"
