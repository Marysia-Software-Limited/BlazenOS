#!/usr/bin/env bash
# scripts/voice-run.sh — hands-free Jessica (S5) on the dev host / Pi.
#
# Starts only the two Rust units that own the mic + wake detection:
#   blazend-audio-in   (real cpal capture → shared-memory ring)
#   blazend-wake       (real openWakeWord "Hej Jessico" → wake.sock)
# then the Python wake runner (`python -m blazend.domains.voice_input.adapters.rpi5.voice`), which reads the
# ring for both wake- and button-triggered captures and does ASR → engine →
# Piper itself. No asr/brain/tts/audio-out units here (the runner covers them),
# so there's no ALSA contention.
#
# Source .env first so OPENAI_API_KEY / GEMINI / BLAZEN_LLM_MODEL are picked up.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
[ -f .env ] && { set -a; . ./.env; set +a; }

VENV="$REPO_ROOT/.venv"
PY="$VENV/bin/python"
CARGO_TARGET="$REPO_ROOT/rpi5/crates/target/debug"
RUN_DIR="$REPO_ROOT/vm-runs/voice-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$RUN_DIR"

export BLAZEN_CONFIG_ROOT="$REPO_ROOT/configs"
export PYTHONPATH="$REPO_ROOT/rpi5/src${PYTHONPATH:+:$PYTHONPATH}"
export BLAZEN_RUNTIME_DIR="${BLAZEN_RUNTIME_DIR:-/tmp/blazen-$UID}"
export BLAZEN_DATA_DIR="${BLAZEN_DATA_DIR:-$REPO_ROOT/vm-runs/jessica-data}"
export BLAZEN_ASR_MODEL="${BLAZEN_ASR_MODEL:-medium}"
export PTT_OUT="${PTT_OUT:-plughw:CARD=wm8960soundcard,DEV=0}"
mkdir -p "$BLAZEN_RUNTIME_DIR"

log() { printf '\033[1;34m[voice-run]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[voice-run]\033[0m %s\n' "$*" >&2; exit 1; }
[ -x "$PY" ] || die "Python venv missing — run 'make python' first"

rm -f "$BLAZEN_RUNTIME_DIR"/*.sock 2>/dev/null || true

# PipeWire/WirePlumber grabs the wm8960 card and breaks cpal's direct-hw
# capture (ALSA POLLERR → blazend-audio-in panics ~5 s in). The appliance image
# runs direct ALSA with no PipeWire; on a dev desktop we suspend it for the
# session and restore it on exit. Output (aplay -D plughw) is direct ALSA, so
# nothing here needs PipeWire.
PW_WAS_UP=0
if command -v systemctl >/dev/null && systemctl --user is-active --quiet pipewire 2>/dev/null; then
  PW_WAS_UP=1
  log "suspending PipeWire/WirePlumber (frees the wm8960 for cpal capture)"
  systemctl --user stop wireplumber.service pipewire.service pipewire.socket 2>/dev/null || true
  sleep 1
fi

if [ ! -x "$CARGO_TARGET/blazend-audio-in" ] || [ ! -x "$CARGO_TARGET/blazend-wake" ] || [ ! -x "$CARGO_TARGET/blazend-player" ]; then
  log "building appliance Rust workspace…"
  (cd "$REPO_ROOT/rpi5/crates" && cargo build --workspace --quiet)
fi

export ORT_DYLIB_PATH="${ORT_DYLIB_PATH:-$(find "$VENV" -name 'libonnxruntime.so.1*' 2>/dev/null | head -1)}"
export BLAZEN_PLAYER="${BLAZEN_PLAYER:-$CARGO_TARGET/blazend-player}"

declare -a PIDS=()
cleanup() {
  log "shutting down"
  for pid in "${PIDS[@]:-}"; do kill -TERM "$pid" 2>/dev/null || true; done
  sleep 0.3
  for pid in "${PIDS[@]:-}"; do kill -KILL "$pid" 2>/dev/null || true; done
  if [ "${PW_WAS_UP:-0}" = "1" ]; then
    log "restoring PipeWire/WirePlumber"
    systemctl --user start pipewire.socket pipewire.service wireplumber.service 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

log "starting blazend-audio-in (mic → ring)"
("$CARGO_TARGET/blazend-audio-in" --device "${BLAZEN_AUDIO_DEVICE:-plughw:CARD=wm8960soundcard,DEV=0}" \
  >>"$RUN_DIR/audio-in.log" 2>&1) & PIDS+=($!)
log "starting blazend-wake (Hej Jessico → wake.sock)"
("$CARGO_TARGET/blazend-wake" >>"$RUN_DIR/wake.log" 2>&1) & PIDS+=($!)

sleep 1.5  # let the ring + wake socket come up
log "starting Python wake runner (blazend.domains.voice_input.adapters.rpi5.voice) — logs at $RUN_DIR/runner.log"
"$PY" -m blazend.domains.voice_input.adapters.rpi5.voice 2>&1 | tee "$RUN_DIR/runner.log"
