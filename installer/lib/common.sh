# shellcheck shell=bash
# Common helpers for the Jessica Linux installer. Sourced by install.sh.
# Everything side-effectful goes through run() so --dry-run prints instead.

log()  { printf '\033[1;32m[install]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[install]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[install]\033[0m %s\n' "$*" >&2; exit 1; }

# Execute (or, under --dry-run, just print) a command.
run() {
  if [ "${DRY_RUN:-0}" = "1" ]; then
    printf '\033[0;36m[dry-run]\033[0m %s\n' "$*"
  else
    "$@"
  fi
}

# Append a created path to the uninstall manifest.
manifest_add() {
  [ "${DRY_RUN:-0}" = "1" ] && return 0
  mkdir -p "$(dirname "$MANIFEST")"
  grep -qxF "$1" "$MANIFEST" 2>/dev/null || echo "$1" >> "$MANIFEST"
}

# render_template SRC DEST — substitute @PLACEHOLDER@ tokens from the current
# path model. sed-based; placeholders that stay unsubstituted are a bug, so we
# fail loudly on any survivor.
render_template() {
  local src="$1" dest="$2" tmp
  tmp="$(mktemp)"
  sed \
    -e "s|@BIN_DIR@|$BIN_DIR|g" \
    -e "s|@VENV_BIN@|$VENV/bin|g" \
    -e "s|@PY_ROOT@|$LIB_DIR|g" \
    -e "s|@RUNTIME_DIR@|$RUNTIME_DIR|g" \
    -e "s|@STATE_DIR@|$STATE_DIR|g" \
    -e "s|@DATA_DIR@|$STATE_DIR/data|g" \
    -e "s|@MODELS_DIR@|$MODELS_DIR|g" \
    -e "s|@CONFIG_ROOT@|$CONFIG_ROOT|g" \
    -e "s|@SITE_DIR@|$SITE_DIR|g" \
    -e "s|@ENV_FILE@|$ENV_FILE|g" \
    -e "s|@AUDIO_ENV_FILE@|$AUDIO_ENV_FILE|g" \
    -e "s|@SECRETS_FILE@|$SITE_DIR/secrets.env|g" \
    -e "s|@MESH_FILE@|$SITE_DIR/mesh.yaml|g" \
    -e "s|@SERVICE_USER_BLOCK@|$SERVICE_USER_BLOCK|g" \
    -e "s|@CPU_PINNING@|$CPU_PINNING|g" \
    -e "s|@HARDENING_BLOCK@|$HARDENING_BLOCK|g" \
    -e "s|@TARGET_WANTEDBY@|$TARGET_WANTEDBY|g" \
    -e "s|@SYSTEMCTL_ENV@|$SYSTEMCTL_ENV|g" \
    -e "s|@NODE_NAME@|$NODE_NAME|g" \
    -e "s|@AUDIO_GROUP@|$AUDIO_GROUP|g" \
    "$src" > "$tmp"
  if grep -qE '@[A-Z_]+@' "$tmp"; then
    die "unsubstituted placeholder in $src: $(grep -oE '@[A-Z_]+@' "$tmp" | sort -u | tr '\n' ' ')"
  fi
  if [ "${DRY_RUN:-0}" = "1" ]; then
    printf '\033[0;36m[dry-run]\033[0m render %s -> %s\n' "$src" "$dest"
    rm -f "$tmp"
  else
    install -D -m 0644 "$tmp" "$dest"
    rm -f "$tmp"
    manifest_add "$dest"
  fi
}

# Detect the distro family and source its lib. The macOS stub keeps the seam
# visible for the future port.
detect_distro() {
  case "$(uname -s)" in
    Darwin) DISTRO=macos ;;
    Linux)
      if command -v pacman >/dev/null 2>&1; then DISTRO=arch
      elif command -v apt-get >/dev/null 2>&1; then DISTRO=debian
      elif command -v dnf >/dev/null 2>&1; then DISTRO=fedora
      else die "unsupported distro: need pacman, apt-get or dnf"
      fi ;;
    *) die "unsupported OS: $(uname -s)" ;;
  esac
  # shellcheck source=/dev/null
  . "$INSTALLER_DIR/lib/distro-$DISTRO.sh"
  log "distro: $DISTRO"
}
