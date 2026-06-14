#!/bin/bash -e
# stage-blazen/00-install/01-run-chroot.sh — runs inside the chroot
# during pi-gen build. Installs blazend-* into the image's rootfs.
#
# Reads from /var/lib/blazen-staging/{src,rust,configs} which pi-gen
# rsync'd in via 00-install/files/var/lib/blazen-staging/* (populated
# by scripts/build-image.sh). systemd unit files come from the
# stage-level stage-blazen/files/ rsync.
#
# We use /var/lib/blazen-staging/ rather than /tmp/ because pi-gen
# mounts a tmpfs on /tmp inside the chroot, wiping anything we put
# there.

STAGE=/var/lib/blazen-staging
INSTALL_DIR=/usr/lib/blazen
mkdir -p "$INSTALL_DIR" "$INSTALL_DIR/bin"

# --- Diagnostics (so failures are debuggable) ----------------------------

echo "=== blazend chroot install ==="
echo "STAGE dir: $STAGE"
echo "Contents of $STAGE (should contain blazen-{src,rust,configs}):"
ls -la "$STAGE" 2>&1 || echo "  (does not exist)"
echo "==="

# --- Python ---------------------------------------------------------------

python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install --upgrade pip wheel
# Lightweight runtime deps. Heavier ML deps (faster-whisper,
# llama-cpp-python) are lazy-installed on first wake or via SSH to keep
# the image under 1 GB.
"$INSTALL_DIR/.venv/bin/pip" install --no-cache-dir \
  "pyyaml>=6.0" \
  "pydantic>=2.9" \
  "numpy>=1.26" \
  "jsonschema>=4.21"

cp -R "$STAGE/blazen-src/blazend" "$INSTALL_DIR/blazend"

# --- Rust binaries (pre-cross-compiled on the host) ----------------------

for bin in blazend-audio-in blazend-audio-out blazend-wake blazend-nlu blazend-tts blazend-health blazend-fabric; do
  install -m 0755 "$STAGE/blazen-rust/$bin" "$INSTALL_DIR/bin/$bin"
done

# --- Default configs ------------------------------------------------------

mkdir -p /usr/share/blazen/defaults /etc/blazen/intents /etc/blazen/overrides
cp -R "$STAGE/blazen-configs/"*.yaml             /usr/share/blazen/defaults/
cp -R "$STAGE/blazen-configs/intents/"*.yaml     /etc/blazen/intents/
cp -R "$STAGE/blazen-configs/vm"                 /usr/share/blazen/defaults/

# --- systemd units --------------------------------------------------------
# (Unit files were rsync'd into /etc/systemd/system/ via stage-blazen/files/.)

systemctl enable blazend.target
for unit in blazend-audio-in blazend-audio-out blazend-wake blazend-asr \
            blazend-nlu blazend-brain blazend-tts blazend-health \
            blazend-orchestrator blazend-bootstrap blazend-fabric; do
  systemctl enable "${unit}.service" 2>/dev/null || true
done

# --- User + permissions ---------------------------------------------------
#
# SSH is ON by default in BOTH flavours (see docs/06-SSH-BOOTSTRAP.md). The
# `blazen` account is a real login user either way; the flavours differ only
# in the SHIPPED CREDENTIAL:
#
#   RELEASE (default) — pubkey-only, **fail-closed**: no password is set and
#     no key is baked in. The operator provisions their own pubkey via
#     /boot/blazen-firstboot/authorized_keys at flash time (blazend-bootstrap
#     installs it to ~blazen/.ssh/). With no key, sshd runs but admits nobody.
#
#   DEV (marker file /var/lib/blazen-staging/DEV_IMAGE present) — additionally
#     bakes the dev pubkey and a known serial-console password (`blazen:blazen`)
#     so the M1 QEMU boot test (`ssh -p 2222 blazen@localhost true`) can pass.
#     These never ship in a release image.

# Login `blazen` user with NOPASSWD sudo (the SSH key is the privilege
# boundary). No password is set here — DEV adds one below.
if ! id blazen >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash --groups audio,plugdev,sudo blazen
else
  usermod --shell /bin/bash --append --groups audio,plugdev,sudo blazen
  [ -d /home/blazen ] || { mkhomedir_helper blazen 2>/dev/null || true; }
fi
install -d -m 0755 /etc/sudoers.d
printf 'blazen ALL=(ALL) NOPASSWD:ALL\n' > /etc/sudoers.d/010-blazen
chmod 0440 /etc/sudoers.d/010-blazen
systemctl enable ssh

if [ -f "$STAGE/DEV_IMAGE" ]; then
  echo "=== blazend chroot: DEV image (login blazen + ssh on + dev creds) ==="
  # Known dev password for the serial console fallback; SSH prefers the
  # baked-in key. Never ships in a release image.
  echo 'blazen:blazen' | chpasswd
  if [ -f "$STAGE/dev_authorized_keys" ]; then
    install -d -m 0700 /home/blazen/.ssh
    install -m 0600 "$STAGE/dev_authorized_keys" /home/blazen/.ssh/authorized_keys
    chown -R blazen:blazen /home/blazen/.ssh
  fi
else
  echo "=== blazend chroot: RELEASE image (login blazen + ssh on, key-only) ==="
  # Fail-closed: lock the password so only an operator-provisioned pubkey
  # (via firstboot) can authenticate. No key ships in the image.
  passwd --lock blazen || true
fi

mkdir -p /var/lib/blazen /run/blazen
chown -R blazen:blazen /var/lib/blazen /run/blazen "$INSTALL_DIR"

# Clean up staging tree (it would otherwise ship in the image).
rm -rf "$STAGE"
