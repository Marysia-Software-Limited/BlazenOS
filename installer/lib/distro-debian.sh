# shellcheck shell=bash
# Debian/Ubuntu (apt) package layer.

PKGS_BASE=(python3 python3-venv python3-pip build-essential cmake pkg-config
           libasound2-dev alsa-utils portaudio19-dev ffmpeg curl git rsync)
PKGS_AUDIO=(pipewire pipewire-alsa wireplumber)

pkg_install() {
  run sudo ${ASSUME_YES:+DEBIAN_FRONTEND=noninteractive} apt-get install \
    ${ASSUME_YES:+-y} "$@"
}

pkg_install_rust() {
  command -v cargo >/dev/null 2>&1 && return 0
  run bash -c 'curl -fsSL https://sh.rustup.rs | sh -s -- -y --default-toolchain stable'
  export PATH="$HOME/.cargo/bin:$PATH"
}

pkg_install_ollama() {
  command -v ollama >/dev/null 2>&1 && return 0
  run bash -c 'curl -fsSL https://ollama.com/install.sh | sh'
}
