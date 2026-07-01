#!/bin/bash
# Bring up a USB CDC-ECM ethernet gadget on the Pi 5 USB-C port so a dev host
# (paul) can SSH to the appliance DIRECTLY over USB (Pi = 10.55.0.1/24),
# independent of the LAN — for Claude Code to test/manage the device.
#
# Requires (set by 01-run-chroot.sh in the image):
#   config.txt:  dtoverlay=dwc2,dr_mode=peripheral
#   cmdline.txt: modules-load=dwc2
# The peer (paul) gets 10.55.0.2/24 via its own NetworkManager profile matching
# the fixed host MAC below; then: ssh beret@10.55.0.1 (Host jessica-usb).
set -e

G=/sys/kernel/config/usb_gadget/blazen
# Already bound? (UDC non-empty) → nothing to do.
if [ -f "$G/UDC" ] && [ -n "$(cat "$G/UDC" 2>/dev/null)" ]; then
  exit 0
fi

modprobe libcomposite 2>/dev/null || true
mkdir -p "$G"
cd "$G"
echo 0x1d6b > idVendor          # Linux Foundation
echo 0x0104 > idProduct         # Multifunction Composite Gadget
echo 0x0100 > bcdDevice
echo 0x0200 > bcdUSB
mkdir -p strings/0x409
SERIAL="$(sed -n 's/^Serial[[:space:]]*:[[:space:]]*//p' /proc/cpuinfo | tail -c 9)"
echo "${SERIAL:-00000000}"     > strings/0x409/serialnumber
echo "Blazen"                  > strings/0x409/manufacturer
echo "Blazen Pi 5 (USB mgmt)"  > strings/0x409/product
mkdir -p configs/c.1/strings/0x409
echo "CDC ECM (SSH over USB)"  > configs/c.1/strings/0x409/configuration
echo 250 > configs/c.1/MaxPower
# CDC-ECM ethernet function with STABLE MACs. host_addr (the peer/paul side) MUST
# match paul's existing `jessica-otg` NetworkManager profile (mac 02:00:00:55:00:02
# → 10.55.0.2) so paul auto-configures the link when the Pi is plugged in.
mkdir -p functions/ecm.usb0
echo "02:00:00:55:00:02" > functions/ecm.usb0/host_addr   # paul side (== jessica-otg profile)
echo "02:00:00:55:00:01" > functions/ecm.usb0/dev_addr    # Pi side
ln -sf functions/ecm.usb0 configs/c.1/
# Bind to the (only) USB device controller — dwc2 in peripheral mode.
UDC="$(ls /sys/class/udc 2>/dev/null | head -1)"
[ -n "$UDC" ] && echo "$UDC" > UDC

# Static IP on the Pi side of the USB link.
for _ in $(seq 1 20); do ip link show usb0 >/dev/null 2>&1 && break; sleep 0.3; done
ip addr add 10.55.0.1/24 dev usb0 2>/dev/null || true
ip link set usb0 up 2>/dev/null || true
