#!/usr/bin/env bash
# run-on-paul.sh — open a Claude Code session on the `paul` build rig and
# continue the dedicated-appliance-image task (Track B in HANDOFF.md).
#
# SSH keys are shared between this Mac and paul, so this only needs a
# reachable `paul` host alias. The launcher: SSHes in (with a TTY), fast-
# forwards the repo, and launches `claude` with a kickoff prompt that points
# it at HANDOFF.md and tells it to build the flashable DEV .img.
#
# Usage:
#   ./run-on-paul.sh                  # default kickoff (build the dev .img)
#   ./run-on-paul.sh "custom prompt"  # override the kickoff prompt
#   BLAZEN_SYNC_HOST=other ./run-on-paul.sh
#
# Env:
#   BLAZEN_SYNC_HOST   remote host alias (default: paul) — mirrors the Makefile
#   BLAZEN_PAUL_REPO   repo path on the remote (default: $HOME/dev/blazen_os)

set -euo pipefail

HOST="${BLAZEN_SYNC_HOST:-paul}"
REMOTE_REPO="${BLAZEN_PAUL_REPO:-\$HOME/dev/blazen_os}"   # \$HOME expands on paul

DEFAULT_PROMPT="You're resuming the blazen_os appliance-image task on the paul build rig. \
FIRST read the top session log entry (2026-06-22, macOS -> paul) in HANDOFF.md — it has the full plan and the locked user decisions. \
Then execute Track B Phase 2: build the flashable DEV SD image. \
SSH keys are SHARED between the Mac and paul, so bake paul's OWN key directly (no scp). \
Steps: (1) the repo was just pulled by the launcher — if the pull failed, reconcile any paul-local divergence first; \
(2) optionally run 'make models' to pre-bundle weights into the image; \
(3) run 'BLAZEN_DEV_SSH_PUBKEY=\$HOME/.ssh/id_ed25519.pub make pi-image-dev' (long: pi-gen + Docker; confirm before starting and track with tasks); \
(4) loopback-verify the rootfs (blazen login shell, ~blazen/.ssh/authorized_keys present, ssh + blazend-* units enabled), \
then STOP and report the .img path under vm-images/ so the maintainer can scp it to the Mac and flash the dedicated card. \
Do NOT flash on paul — there is no SD reader there."

PROMPT="${1:-$DEFAULT_PROMPT}"

# Encode the prompt so any quoting in it survives the SSH hop untouched.
PROMPT_B64=$(printf '%s' "$PROMPT" | base64 | tr -d '\n')

# Remote script: quoted heredoc → nothing expands locally; $HOME / $(…) run on paul.
REMOTE_SCRIPT=$(cat <<'EOS'
set -e
cd "__REMOTE_REPO__"
echo "[paul] $(pwd) — fast-forwarding from origin…"
git pull --ff-only origin main || { echo "[paul] pull not clean — reconcile paul-local changes, then re-run." >&2; exit 1; }
command -v claude >/dev/null 2>&1 || { echo "[paul] ERROR: 'claude' not on PATH in a login shell. Install Claude Code on paul (or fix PATH), then re-run." >&2; exit 127; }
echo "[paul] launching Claude Code to continue the task…"
exec claude "$(printf %s '__PROMPT_B64__' | base64 -d)"
EOS
)
REMOTE_SCRIPT=${REMOTE_SCRIPT/__REMOTE_REPO__/$REMOTE_REPO}
REMOTE_SCRIPT=${REMOTE_SCRIPT/__PROMPT_B64__/$PROMPT_B64}

# Ship the whole remote script base64-encoded too, decoded as the bash -lc
# argument (keeps stdin = the TTY so Claude stays interactive).
RS_B64=$(printf '%s' "$REMOTE_SCRIPT" | base64 | tr -d '\n')

echo "[run-on-paul] connecting to '$HOST' …"
exec ssh -t "$HOST" "bash -lc \"\$(printf %s '$RS_B64' | base64 -d)\""
