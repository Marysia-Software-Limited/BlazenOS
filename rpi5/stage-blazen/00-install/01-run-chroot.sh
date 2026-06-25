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
# Lightweight runtime deps always go in.
"$INSTALL_DIR/.venv/bin/pip" install --no-cache-dir \
  "pyyaml>=6.0" \
  "pydantic>=2.9" \
  "numpy>=1.26" \
  "jsonschema>=4.21"
# Heavy ML wheels (faster-whisper, llama-cpp-python, onnxruntime, piper-tts):
# baked OFFLINE from a pre-built aarch64 wheelhouse when present (the
# "full offline stack" image). Otherwise they stay lazy-installed on first
# wake / via SSH and the image remains <1 GB. The wheelhouse is built on a
# native aarch64 host (`pip wheel "rpi5[runtime]"`) and staged by
# build-image.sh — NEVER compiled in this emulated chroot (llama.cpp would
# take hours under qemu).
if [ -d "$STAGE/blazen-wheels" ] && [ -n "$(ls -A "$STAGE/blazen-wheels" 2>/dev/null)" ]; then
  echo "=== blazend chroot: installing ML runtime from offline wheelhouse ==="
  "$INSTALL_DIR/.venv/bin/pip" install --no-cache-dir --no-index \
    --find-links "$STAGE/blazen-wheels" \
    faster-whisper llama-cpp-python onnxruntime piper-tts sounddevice soundfile tokenizers spidev
fi

cp -R "$STAGE/blazen-src/blazend" "$INSTALL_DIR/blazend"

# --- Rust binaries (pre-cross-compiled on the host) ----------------------

for bin in blazend-audio-in blazend-audio-out blazend-wake blazend-nlu blazend-tts blazend-health blazend-fabric blazend-player; do
  install -m 0755 "$STAGE/blazen-rust/$bin" "$INSTALL_DIR/bin/$bin"
done

# --- Default configs ------------------------------------------------------

mkdir -p /usr/share/blazen/defaults /etc/blazen/intents /etc/blazen/overrides
cp -R "$STAGE/blazen-configs/"*.yaml             /usr/share/blazen/defaults/
cp -R "$STAGE/blazen-configs/intents/"*.yaml     /etc/blazen/intents/
cp -R "$STAGE/blazen-configs/vm"                 /usr/share/blazen/defaults/

# --- Baked model weights (the "full offline stack") ----------------------
# Staged by build-image.sh from the host models/ tree into
# $STAGE/blazen-models/{llm,asr,tts,wake}. When present, a freshly-flashed
# card runs the whole voice loop with NO network on first boot. When absent
# the models lazy-download on first use (lean image).
if [ -d "$STAGE/blazen-models" ]; then
  echo "=== blazend chroot: baking model weights into /var/lib/blazen/models ==="
  mkdir -p /var/lib/blazen/models
  cp -R "$STAGE/blazen-models/"* /var/lib/blazen/models/
  du -sh /var/lib/blazen/models/* 2>/dev/null || true
fi

# --- systemd units --------------------------------------------------------
# (Unit files were rsync'd into /etc/systemd/system/ via stage-blazen/files/.)

systemctl enable blazend.target
# NOTE: blazend-wake is deliberately NOT enabled. The shipped synthetic
# `jessica.onnx` wake model scores ~0 on real speech, so the appliance runs in
# always-listen mode (`wake-word.yaml require_wake: false`) and the wake
# detector would only burn a whole CPU core (~150 %) running onnxruntime to no
# effect. Re-enable it once a real wake model exists. See docs/05-MODELS.md.
for unit in blazend-hat-mixer blazend-audio-in blazend-audio-out blazend-asr \
            blazend-nlu blazend-brain blazend-tts blazend-health \
            blazend-orchestrator blazend-bootstrap blazend-fabric blazend-button; do
  systemctl enable "${unit}.service" 2>/dev/null || true
done

# PipeWire/PulseAudio must NOT run: the blazend audio path owns the WM8960 via
# direct ALSA (arecord/aplay on plughw). A stray user PipeWire session probes
# the codec in a retry loop (wireplumber ~50 % CPU) and starves the real-time
# capture, spiking the mic noise floor into the speech band. Mask it globally
# for every user so it can't socket-activate. (Harmless if not installed.)
systemctl --global mask pipewire.socket pipewire-pulse.socket pipewire.service \
  pipewire-pulse.service wireplumber.service filter-chain.service 2>/dev/null || true
echo "=== blazend chroot: wake disabled (no real model) + PipeWire masked ==="

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
  useradd --create-home --shell /bin/bash --groups audio,plugdev,sudo,spi,gpio,i2c blazen
else
  usermod --shell /bin/bash --append --groups audio,plugdev,sudo,spi,gpio,i2c blazen
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

# --- Audio HAT ready out-of-the-box (ReSpeaker 2-Mics / WM8960) -----------
# Enable the codec overlay + I2C so capture works on first boot with no
# manual dtoverlay edit. The mixer state (capture path unmuted, boost set)
# ships as /var/lib/alsa/asound.state via stage-blazen/files and is restored
# by alsa-restore.service on boot.
CFG=/boot/firmware/config.txt
if [ -f "$CFG" ]; then
  grep -q '^dtparam=i2c_arm=on'        "$CFG" || sed -i 's/^#dtparam=i2c_arm=on/dtparam=i2c_arm=on/' "$CFG"
  grep -q '^dtparam=i2c_arm=on'        "$CFG" || echo 'dtparam=i2c_arm=on' >> "$CFG"
  # SPI0 (/dev/spidev0.0) drives the HAT's 3 APA102 status LEDs.
  grep -q '^dtparam=spi=on'            "$CFG" || sed -i 's/^#dtparam=spi=on/dtparam=spi=on/' "$CFG"
  grep -q '^dtparam=spi=on'            "$CFG" || echo 'dtparam=spi=on' >> "$CFG"
  if ! grep -q 'dtoverlay=wm8960-soundcard' "$CFG"; then
    printf '\n# Blazen: ReSpeaker 2-Mics WM8960 audio HAT\ndtoverlay=wm8960-soundcard\n' >> "$CFG"
  fi
  echo "=== blazend chroot: WM8960 HAT overlay + SPI (APA102 LEDs) enabled in config.txt ==="
fi
systemctl enable alsa-restore.service 2>/dev/null || true

# --- No swap (RAM-only appliance) ----------------------------------------
# The voice stack is sized to fit physical RAM; swapping would only add SD
# wear + latency. Disable the zram-generator swap and mask swap.target so
# nothing re-creates it. Matches the live tuning on the dev card.
printf '[zram0]\nzram-size = 0\n' > /etc/systemd/zram-generator.conf
systemctl mask dev-zram0.swap systemd-zram-setup@zram0.service swap.target 2>/dev/null || true
echo "=== blazend chroot: swap disabled (zram size 0 + swap.target masked) ==="

# Clean up staging tree (it would otherwise ship in the image).
rm -rf "$STAGE"
