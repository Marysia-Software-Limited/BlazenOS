#!/usr/bin/env bash
# record-wake-samples.sh — capture "dżesika" wake POSITIVES + room NEGATIVES on the
# Jabra, for retraining models/wake/jessica.onnx (scripts/train-wake.py --real-dir).
#
# Voice-guided: a HIGH beep = "speak now", a LOW beep = "done" — so you can record
# without watching the screen. Run it in an interactive shell (needs a TTY for the
# Enter prompts + sudo).
#
#   ./record-wake-samples.sh positives [N]           # N "dżesika" clips (default 30)
#   ./record-wake-samples.sh negatives [SECS CLIPS]  # room noise (default 20s × 6)
#
# Positives: after the HIGH beep, say ONLY the wake word: "dżesika".
# Negatives: after the HIGH beep, talk normally / leave the TV on — but do NOT
#            say "dżesika" (this is what teaches it to ignore the room).
set -euo pipefail

DEV="plughw:CARD=USB,DEV=0"          # Jabra SPEAK 410, capture + playback
BASE="$HOME/wake-samples"
POS="$BASE/dzesika"
NEG="$BASE/negatives"
BEEP_HI="$BASE/.beep_hi.wav"
BEEP_LO="$BASE/.beep_lo.wav"
mkdir -p "$POS" "$NEG"

# One-time: synth two short beeps (needs python3, present on the Pi).
make_beep() {  # freq path
  python3 - "$1" "$2" <<'PY'
import math, struct, sys, wave
freq, path = float(sys.argv[1]), sys.argv[2]
rate, dur = 16000, 0.15
with wave.open(path, "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
    n = int(rate * dur)
    fr = bytearray()
    for i in range(n):
        env = min(1.0, i / (0.02 * rate), (n - i) / (0.02 * rate))  # soft edges
        fr += struct.pack("<h", int(0.4 * env * 32767 * math.sin(2 * math.pi * freq * i / rate)))
    w.writeframes(bytes(fr))
PY
}
[ -f "$BEEP_HI" ] || make_beep 1180 "$BEEP_HI"
[ -f "$BEEP_LO" ] || make_beep 620  "$BEEP_LO"
beep() { aplay -q -D "$DEV" "$1" 2>/dev/null || true; }

pipeline_down() {
  echo ">> stopping voice pipeline (frees the Jabra mic)…"
  sudo systemctl stop blazend.target || true
  sleep 3
}
pipeline_up() {
  echo ">> restarting voice pipeline (stop→sleep→start already done)…"
  sudo systemctl start blazend.target || true
  echo ">> pipeline back up."
}

record_one() {  # path seconds
  beep "$BEEP_HI"
  arecord -q -D "$DEV" -f S16_LE -r 16000 -c 1 -d "$2" "$1"
  beep "$BEEP_LO"
}

mode="${1:-help}"
case "$mode" in
positives)
  N="${2:-30}"
  pipeline_down; trap pipeline_up EXIT
  echo "── Recording $N 'dżesika' clips. HIGH beep → say: dżesika ──"
  i=0
  while [ "$i" -lt "$N" ]; do
    i=$((i + 1))
    f=$(printf "%s/pos_%02d.wav" "$POS" "$i")
    read -rp "[$i/$N] Enter, then say 'dżesika' on the beep… " _
    record_one "$f" 2
    echo "  saved $(basename "$f")"
  done
  echo "✓ positives: $(ls "$POS"/*.wav 2>/dev/null | wc -l) clips in $POS"
  ;;
negatives)
  SECS="${2:-20}"; CLIPS="${3:-6}"
  pipeline_down; trap pipeline_up EXIT
  echo "── Recording $CLIPS × ${SECS}s of ROOM sound. Talk normally / TV on — NO 'dżesika' ──"
  i=0
  while [ "$i" -lt "$CLIPS" ]; do
    i=$((i + 1))
    f=$(printf "%s/neg_%02d.wav" "$NEG" "$i")
    read -rp "[$i/$CLIPS] Enter to record ${SECS}s of ambient on the beep… " _
    record_one "$f" "$SECS"
    echo "  saved $(basename "$f")"
  done
  echo "✓ negatives: $(ls "$NEG"/*.wav 2>/dev/null | wc -l) clips in $NEG"
  ;;
*)
  echo "usage: $0 positives [N]             # record N 'dżesika' clips (default 30)"
  echo "       $0 negatives [SECS CLIPS]    # record room noise (default 20s × 6)"
  echo "HIGH beep = speak now · LOW beep = done"
  ;;
esac
