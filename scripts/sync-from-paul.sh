#!/usr/bin/env bash
# scripts/sync-from-paul.sh — pull the blazen_os source tree from paul
# back to this host (macOS), so the macOS Claude session sees any
# changes paul Claude landed.
#
# Mirror of scripts/sync-to-paul.sh in the other direction.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${BLAZEN_SYNC_HOST:-paul}"
SRC="${BLAZEN_SYNC_DEST:-~/dev/blazen_os/}"

log() { printf '\033[1;34m[sync-from-paul]\033[0m %s\n' "$*"; }

log "pulling $HOST:$SRC -> $REPO_ROOT/"

# --update means files only get replaced when the source is newer; this
# prevents stomping on macOS-side WIP that paul hasn't seen yet.
rsync -avz --update \
  --exclude='.venv/' \
  --exclude='crates/target/' \
  --exclude='vm-images/' \
  --exclude='models/' \
  --exclude='_test_projects/' \
  --exclude='build/' \
  --exclude='vm-runs/' \
  --exclude='logs/' \
  --exclude='.idea/' \
  --exclude='.pytest_cache/' \
  --exclude='.ruff_cache/' \
  --exclude='.DS_Store' \
  --exclude='__pycache__/' \
  --exclude='*.egg-info/' \
  --exclude='.git/' \
  "$HOST:$SRC" "$REPO_ROOT/"

log "done. Quick sanity:"
log "  cd $REPO_ROOT && make test-fast"
