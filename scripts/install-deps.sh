#!/usr/bin/env bash
# scripts/install-deps.sh — install host-side toolchain for blazen_os.
#
# This is a developer-machine setup script, NOT something you run on
# the Pi. It prepares qemu, a Python venv, audio libs, and the
# build-image pipeline. Idempotent.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$REPO_ROOT/.venv"

log()  { printf '\033[1;34m[install-deps]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[install-deps]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[install-deps] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

detect_os() {
  case "$(uname -s)" in
    Linux)  HOST_OS=linux ;;
    Darwin) HOST_OS=macos ;;
    *)      die "Unsupported host OS: $(uname -s)" ;;
  esac
}

install_macos() {
  command -v brew >/dev/null || die "Homebrew required (brew.sh)"
  brew bundle --no-lock --file=/dev/stdin <<EOF
brew "python@3.11"
brew "qemu"
brew "cmake"
brew "ninja"
brew "git"
brew "wget"
brew "pkg-config"
brew "portaudio"
brew "ffmpeg"
brew "piper-tts" || nil
EOF
  install_rust
}

install_linux() {
  if command -v apt-get >/dev/null; then
    sudo apt-get update
    sudo apt-get install -y \
      python3 python3-venv python3-pip \
      qemu-system-arm qemu-utils \
      build-essential cmake ninja-build \
      git wget pkg-config curl \
      libportaudio2 portaudio19-dev \
      libasound2-dev libssl-dev \
      ffmpeg \
      pipewire pipewire-audio wireplumber
  elif command -v dnf >/dev/null; then
    sudo dnf install -y \
      python3 python3-virtualenv \
      qemu-system-aarch64 qemu-img \
      gcc gcc-c++ cmake ninja-build \
      git wget pkgconf curl \
      portaudio-devel alsa-lib-devel openssl-devel ffmpeg \
      pipewire wireplumber
  else
    die "Unsupported Linux package manager (need apt or dnf)"
  fi
  install_rust
}

install_rust() {
  if ! command -v cargo >/dev/null; then
    log "Installing Rust via rustup"
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable
    # shellcheck disable=SC1091
    . "$HOME/.cargo/env"
  fi
  log "Rust toolchain:"
  rustc --version
  cargo --version
  # Pi 5 cross-compilation target (idempotent).
  rustup target add aarch64-unknown-linux-gnu || true
  # `cross` is the easiest cross-compiler if Docker/podman is available.
  if command -v docker >/dev/null || command -v podman >/dev/null; then
    cargo install --locked cross || warn "cross install failed; manual cross-compile required"
  else
    warn "Docker/Podman not detected — install one for \`cross\` to work, or set up a sysroot manually"
  fi
}

create_venv() {
  if [ ! -x "$VENV/bin/python" ]; then
    log "Creating venv at $VENV"
    python3 -m venv "$VENV"
  fi
  "$VENV/bin/pip" install --upgrade pip wheel
  "$VENV/bin/pip" install \
    "pyyaml>=6.0" \
    "pydantic>=2.9" \
    "numpy>=1.26" \
    "sounddevice>=0.5" \
    "soundfile>=0.12" \
    "faster-whisper>=1.0" \
    "piper-tts>=1.2" \
    "pytest>=8.0" \
    "rich>=13.7"
}

main() {
  detect_os
  log "Host: $HOST_OS"
  if [ "$HOST_OS" = "macos" ]; then install_macos; else install_linux; fi
  create_venv
  log "Done. Activate with: source $VENV/bin/activate"
}

main "$@"
