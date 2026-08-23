# shellcheck shell=bash
# Arch Linux (pacman) package layer.

PKGS_BASE=(python python-pip base-devel cmake pkgconf alsa-lib alsa-utils
           portaudio ffmpeg curl git rsync)
PKGS_AUDIO=(pipewire pipewire-alsa wireplumber)

pkg_install() {
  run sudo pacman -S --needed ${ASSUME_YES:+--noconfirm} "$@"
}

pkg_install_rust() {
  command -v cargo >/dev/null 2>&1 && return 0
  pkg_install rustup
  run rustup default stable
}

pkg_install_ollama() {
  command -v ollama >/dev/null 2>&1 && return 0
  pkg_install ollama
}
