# shellcheck shell=bash
# Fedora (dnf) package layer.

PKGS_BASE=(python3 python3-pip gcc gcc-c++ make cmake pkgconf-pkg-config
           alsa-lib-devel alsa-utils portaudio-devel ffmpeg-free curl git rsync)
PKGS_AUDIO=(pipewire pipewire-alsa-plugins wireplumber)

pkg_install() {
  run sudo dnf install ${ASSUME_YES:+-y} "$@"
}

pkg_install_rust() {
  command -v cargo >/dev/null 2>&1 && return 0
  pkg_install rust cargo
}

pkg_install_ollama() {
  command -v ollama >/dev/null 2>&1 && return 0
  run bash -c 'curl -fsSL https://ollama.com/install.sh | sh'
}
