#!/usr/bin/env bash
# scripts/headless-wifi.sh — inject WiFi + SSH key + bootstrap config into
# the boot partition of an already-flashed SD card. Run BEFORE first boot.

set -euo pipefail

BOOT_MOUNT=""
SSID="${BLAZEN_WIFI_SSID:-}"
PSK="${BLAZEN_WIFI_PSK:-}"
COUNTRY="${BLAZEN_WIFI_COUNTRY:-PL}"
SSH_KEY="${BLAZEN_AUTHORIZED_SSH_KEY:-}"
USER="${BLAZEN_DEFAULT_USER:-blazen}"

usage() {
  cat <<USAGE
Usage: $0 --boot <mount> [--ssid X] [--psk Y] [--country PL] [--ssh-key '...']

SSH is on by default in the image (pubkey-only); the authorized_keys you
pass here is the operator key that makes the device reachable. Without it
sshd runs but admits nobody (fail-closed).

Writes:
  <boot>/blazen-firstboot/wpa_supplicant.conf
  <boot>/blazen-firstboot/authorized_keys (your operator pubkey)
  <boot>/blazen-firstboot/ssh             (empty — sshd already on; kept for compat)
  <boot>/blazen-firstboot/blazen-bootstrap.yaml
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --boot) BOOT_MOUNT="$2"; shift 2 ;;
    --ssid) SSID="$2"; shift 2 ;;
    --psk) PSK="$2"; shift 2 ;;
    --country) COUNTRY="$2"; shift 2 ;;
    --ssh-key) SSH_KEY="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 1 ;;
  esac
done

[ -z "$BOOT_MOUNT" ] && { usage; exit 1; }
[ -d "$BOOT_MOUNT" ] || { echo "boot mount not found: $BOOT_MOUNT"; exit 1; }

OUT="$BOOT_MOUNT/blazen-firstboot"
mkdir -p "$OUT"

if [ -n "$SSID" ]; then
  cat > "$OUT/wpa_supplicant.conf" <<EOF
country=$COUNTRY
ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
update_config=1
network={
  ssid="$SSID"
  psk="$PSK"
}
EOF
  chmod 600 "$OUT/wpa_supplicant.conf"
  echo "wrote wpa_supplicant.conf"
fi

if [ -n "$SSH_KEY" ]; then
  printf '%s\n' "$SSH_KEY" > "$OUT/authorized_keys"
  chmod 600 "$OUT/authorized_keys"
  echo "wrote authorized_keys"
fi

touch "$OUT/ssh"
echo "wrote ssh marker (sshd is already on by default)"

cat > "$OUT/blazen-bootstrap.yaml" <<EOF
version: 1
user: $USER
keep_ssh_on: true
hostname: blazen
locale: en_US.UTF-8
timezone: Europe/Warsaw
EOF
echo "wrote blazen-bootstrap.yaml"

echo "Done. Eject the card and boot the Pi."
