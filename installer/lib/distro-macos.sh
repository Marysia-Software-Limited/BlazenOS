# shellcheck shell=bash
# macOS stub — the platform seam for the future port (launchd instead of
# systemd, CoreAudio instead of ALSA, Homebrew packages). Kept so install.sh's
# detection path and this file's function surface define the porting contract.

PKGS_BASE=()
PKGS_AUDIO=()

pkg_install()        { die "macOS support is not implemented yet"; }
pkg_install_rust()   { die "macOS support is not implemented yet"; }
pkg_install_ollama() { die "macOS support is not implemented yet"; }
