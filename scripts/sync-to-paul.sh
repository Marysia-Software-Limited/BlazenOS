#!/usr/bin/env bash
# scripts/sync-to-paul.sh — rsync the working tree to the Linux build
# box (defaults to 'paul'). Excludes build artefacts. See docs/15
# (workflow) + docs/16 (sync protocol).
#
# Refuses to push if local tests are red unless --force is given.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${BLAZEN_SYNC_HOST:-paul}"
DEST="${BLAZEN_SYNC_DEST:-~/dev/blazen_os/}"
FORCE=0

while [ $# -gt 0 ]; do
  case "$1" in
    --force) FORCE=1; shift ;;
    -h|--help)
      cat <<USAGE
Usage: $0 [--force]

Pushes blazen_os to \$BLAZEN_SYNC_HOST (default: paul). Without --force,
runs make test-fast first and refuses to push if tests fail.
USAGE
      exit 0
      ;;
    *) shift ;;
  esac
done

log() { printf '\033[1;34m[sync-to-paul]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[sync-to-paul] %s\033[0m\n' "$*" >&2; exit 1; }

if [ "$FORCE" -ne 1 ]; then
  log "pre-flight: make test-fast"
  if ! (cd "$REPO_ROOT" && make test-fast >/tmp/sync-tests.log 2>&1); then
    die "tests failed; not pushing. See /tmp/sync-tests.log. Use --force to override."
  fi
  log "tests green; pushing."
fi

log "syncing $REPO_ROOT/ -> $HOST:$DEST"

rsync -avz --delete \
  --exclude='.venv/' \
  --exclude='target/' \
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
  "$REPO_ROOT/" "$HOST:$DEST"

log "done. To run a build over there:"
log "  ssh $HOST 'cd ~/dev/blazen_os && make test-fast'"
log "  ssh $HOST 'cd ~/dev/blazen_os && make vm-image'"
