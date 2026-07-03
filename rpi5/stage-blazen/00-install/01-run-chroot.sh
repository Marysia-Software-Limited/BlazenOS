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
# blazend-wake IS enabled: a REAL "dżesika" wake model now ships
# (models/wake/jessica.onnx, sha256 ce2527…, trained locally on real negatives +
# the operator's own utterances). The ASR capture is wake-triggered — the ASR
# subscribes to `wake.detected` and reads a fixed window around each hit — so the
# wake detector is a hard dependency of the voice path, not an optional gate.
# Threshold/cooldown live in blazend-wake.service (0.7 / 3000 ms). See
# docs/05-MODELS.md and configs/wake-word.yaml.
for unit in blazend-audio-in blazend-audio-out blazend-wake blazend-asr \
            blazend-nlu blazend-brain blazend-tts blazend-health \
            blazend-orchestrator blazend-bootstrap blazend-fabric blazend-button; do
  systemctl enable "${unit}.service" 2>/dev/null || true
done

# PipeWire/PulseAudio must NOT run: the blazend audio path owns the Jabra USB
# device via direct ALSA (arecord/aplay on plughw:CARD=USB). A stray user
# PipeWire session probes the codec in a retry loop (wireplumber ~50 % CPU) and
# starves the real-time capture, spiking the mic noise floor into the speech
# band. Mask it globally for every user so it can't socket-activate. (Harmless
# if not installed.)
systemctl --global mask pipewire.socket pipewire-pulse.socket pipewire.service \
  pipewire-pulse.service wireplumber.service filter-chain.service 2>/dev/null || true
echo "=== blazend chroot: wake ENABLED (real dżesika model) + PipeWire masked ==="

# Sudoers files must be 0440 root:root or sudo refuses to load them. The rule
# itself (files/etc/sudoers.d/blazen-audio-out) lets the orchestrator hand the
# HAT speaker between TTS and the radio player.
chmod 0440 /etc/sudoers.d/blazen-audio-out 2>/dev/null || true
echo "=== blazend chroot: audio-out sudoers rule installed (radio HAT hand-off) ==="

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

# Operator login `beret` (the maintainer's account — the blazend services still
# run as `blazen`). Same admin groups + NOPASSWD sudo; the SSH key is seeded into
# ~beret/.ssh/authorized_keys in the DEV block below. A MISSING
# ~beret/.ssh/authorized_keys has blocked SSH-after-reboot before, so it is baked
# here rather than left to firstboot.
if ! id beret >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash --groups audio,plugdev,sudo,spi,gpio,i2c beret
else
  usermod --shell /bin/bash --append --groups audio,plugdev,sudo,spi,gpio,i2c beret
  [ -d /home/beret ] || { mkhomedir_helper beret 2>/dev/null || true; }
fi
printf 'beret ALL=(ALL) NOPASSWD:ALL\n' > /etc/sudoers.d/011-beret
chmod 0440 /etc/sudoers.d/011-beret
systemctl enable ssh

if [ -f "$STAGE/DEV_IMAGE" ]; then
  echo "=== blazend chroot: DEV image (login blazen + beret + ssh on + dev creds) ==="
  # Known dev passwords for the serial console fallback; SSH prefers the
  # baked-in key. Never ships in a release image.
  echo 'blazen:blazen' | chpasswd
  echo 'beret:beret' | chpasswd
  if [ -f "$STAGE/dev_authorized_keys" ]; then
    for u in blazen beret; do
      install -d -m 0700 "/home/$u/.ssh"
      install -m 0600 "$STAGE/dev_authorized_keys" "/home/$u/.ssh/authorized_keys"
      chown -R "$u:$u" "/home/$u/.ssh"
    done
  fi
else
  echo "=== blazend chroot: RELEASE image (login blazen + beret + ssh on, key-only) ==="
  # Fail-closed: lock the passwords so only an operator-provisioned pubkey
  # (via firstboot) can authenticate. No key ships in the image.
  passwd --lock blazen || true
  passwd --lock beret || true
fi

# --- System locale (English) + keyboard (Polish) --------------------------
# The maintainer's setup: English UI/locale, physical Polish keyboard. This is
# the OS locale for the shell/SSH only — the VOICE language stays Polish-first at
# runtime (languages.enabled: [pl]); the two are independent.
if [ -f /etc/locale.gen ]; then
  sed -i 's/^# *\(en_US.UTF-8 UTF-8\)/\1/' /etc/locale.gen
  grep -q '^en_US.UTF-8 UTF-8' /etc/locale.gen || echo 'en_US.UTF-8 UTF-8' >> /etc/locale.gen
  locale-gen 2>/dev/null || true
fi
echo 'LANG=en_US.UTF-8' > /etc/default/locale
cat > /etc/default/keyboard <<'KBD'
XKBMODEL="pc105"
XKBLAYOUT="pl"
XKBVARIANT=""
XKBOPTIONS=""
BACKSPACE="guess"
KBD
echo "=== blazend chroot: locale en_US.UTF-8 + keyboard pl ==="

mkdir -p /var/lib/blazen /run/blazen
chown -R blazen:blazen /var/lib/blazen /run/blazen "$INSTALL_DIR"

# --- Jabra-only audio + USB-C SSH-over-USB gadget + status LED ------------
# The appliance mic/speaker is the Jabra SPEAK 410 USB speakerphone — no
# ReSpeaker HAT — so NO wm8960 overlay / I2C. Instead: (1) put the USB-C port in
# peripheral mode so a dev host can SSH to the Pi DIRECTLY over USB
# (blazen-usb-gadget.service brings up usb0 = 10.55.0.1), alongside the LAN; and
# (2) keep SPI0 on for the status LED — a single WS2812/NeoPixel on BCM10 (MOSI)
# shows Jessica's state (led_hw.Ws2812Led). Fail-soft: no LED wired → led.json only.
CFG=/boot/firmware/config.txt
if [ -f "$CFG" ]; then
  if ! grep -q '^dtoverlay=dwc2,dr_mode=peripheral' "$CFG"; then
    printf '\n# Blazen: USB-C peripheral mode for SSH-over-USB (blazen-usb-gadget)\ndtoverlay=dwc2,dr_mode=peripheral\n' >> "$CFG"
  fi
  if ! grep -q '^dtparam=spi=on' "$CFG"; then
    printf '\n# Blazen: SPI0 for the WS2812/APA102 status LED (DIN→BCM10 MOSI)\ndtparam=spi=on\n' >> "$CFG"
  fi
  echo "=== blazend chroot: dwc2 peripheral + SPI status-LED enabled in config.txt ==="
fi
# dwc2 must load early for the gadget to bind its UDC.
CMD=/boot/firmware/cmdline.txt
if [ -f "$CMD" ] && ! grep -q 'modules-load=dwc2' "$CMD"; then
  sed -i '1 s/$/ modules-load=dwc2/' "$CMD"
fi
systemctl enable alsa-restore.service 2>/dev/null || true
systemctl enable blazen-usb-gadget.service 2>/dev/null || true

# --- No swap (RAM-only appliance) ----------------------------------------
# The voice stack is sized to fit physical RAM; swapping would only add SD
# wear + latency. Disable the zram-generator swap and mask swap.target so
# nothing re-creates it. Matches the live tuning on the dev card.
printf '[zram0]\nzram-size = 0\n' > /etc/systemd/zram-generator.conf
systemctl mask dev-zram0.swap systemd-zram-setup@zram0.service swap.target 2>/dev/null || true
echo "=== blazend chroot: swap disabled (zram size 0 + swap.target masked) ==="

# Clean up staging tree (it would otherwise ship in the image).
rm -rf "$STAGE"
