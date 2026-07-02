"""APA102 hardware LED driver — paints the colour contract onto the HAT.

The orchestrator's :class:`blazend.domains.systems.adapters.rpi5.led.LedSimulator` derives the active status
colour from the live event stream and writes it to ``/run/blazen/led.json`` —
that file is the single colour *contract* (see ``docs/02-HARDWARE.md``). On the
reference build the ReSpeaker 2-Mics Pi HAT V2.0 exposes 3× APA102 RGB LEDs on
**SPI0** (BCM10 MOSI / BCM11 SCLK → ``/dev/spidev0.0``); this module paints the
identical contract colour across those LEDs.

It is **fail-soft by design**: with no SPI device (the VM / a dev host, or a
build with ``dtparam=spi=on`` not set) :func:`open_status_led` returns a
:class:`NullStatusLed` no-op and ``led.json`` stays the only status surface.
That keeps the CPU/headless path the contract — the HAT LEDs are a
strict-improvement, never a precondition.

Colour vocabulary is imported from :mod:`blazend.domains.systems.adapters.rpi5.led` so there is exactly one
source of truth for "green = listening", "red = error", etc.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Protocol, runtime_checkable

from blazend.domains.systems.adapters.rpi5.led import BLUE, GREEN, MAGENTA, OFF, RED, YELLOW

log = logging.getLogger("blazend.domains.systems.adapters.rpi5.led_hw")

# Contract colour → 8-bit (R, G, B). Keep the keys in sync with blazend.domains.systems.adapters.rpi5.led.
RGB: dict[str, tuple[int, int, int]] = {
    OFF: (0, 0, 0),
    GREEN: (0, 255, 0),
    BLUE: (0, 0, 255),
    MAGENTA: (255, 0, 255),
    YELLOW: (255, 255, 0),
    RED: (255, 0, 0),
}

# Defaults — overridable via env (see docs/07-CONFIGURATION.md).
DEFAULT_COUNT = 3            # 3 RGB LEDs on the ReSpeaker 2-Mics HAT V2.0
DEFAULT_BRIGHTNESS = 8       # APA102 global brightness, 0..31 (~25 %); gentle
DEFAULT_SPEED_HZ = 8_000_000  # APA102 tolerates ~20 MHz; 8 MHz is comfortable


def apa102_frame(
    pixels: list[tuple[int, int, int]],
    *,
    brightness: int = DEFAULT_BRIGHTNESS,
    order: str = "bgr",
) -> bytes:
    """Build the raw SPI byte stream for a chain of APA102 LEDs.

    Wire format: a 4-byte start frame of zeros, one 4-byte LED frame each
    (``0b111`` + 5-bit global brightness, then the colour channels in ``order``
    — the chip's native order after the brightness byte is **blue, green,
    red**), and an end frame of ``ceil(n/16)`` zero bytes to clock the last
    pixel out. Pure function: no hardware, fully unit-testable.
    """
    bright = 0xE0 | (max(0, min(31, brightness)) & 0x1F)
    out = bytearray(4)  # start frame: 32 zero bits
    for r, g, b in pixels:
        chan = {"r": r & 0xFF, "g": g & 0xFF, "b": b & 0xFF}
        out.append(bright)
        out.extend(chan[c] for c in order)
    out.extend(bytes((len(pixels) + 15) // 16))  # end frame
    return bytes(out)


# --- WS2812 / NeoPixel over SPI MOSI ------------------------------------
# The reliable way to drive a WS2812 on a Pi 5 (its PWM/DMA changed, so the old
# rpi_ws281x doesn't work): clock the data out of SPI MOSI (BCM10). Each WS2812
# bit becomes 3 SPI bits at ~2.4 MHz (bit ~417 ns) — a '1' is 110 (0.83 µs high),
# a '0' is 100 (0.42 µs high), inside the WS2812 timing window. One colour byte =
# 24 SPI bits = 3 SPI bytes; a trailing run of zeros gives the >50 µs reset.
WS2812_SPEED_HZ = 2_400_000
WS2812_DEFAULT_BRIGHTNESS = 40   # 0..255 (~16 %); gentle for a desk indicator
_WS_RESET = bytes(40)            # ~133 µs low at 2.4 MHz >> the 50 µs latch


def _ws_byte_to_spi(v: int) -> bytes:
    acc = 0
    for i in range(8):
        acc = (acc << 3) | (0b110 if (v >> (7 - i)) & 1 else 0b100)
    return acc.to_bytes(3, "big")


_WS_TABLE: list[bytes] = [_ws_byte_to_spi(v) for v in range(256)]


def ws2812_frame(
    pixels: list[tuple[int, int, int]],
    *,
    brightness: int = WS2812_DEFAULT_BRIGHTNESS,
    order: str = "grb",
) -> bytes:
    """Build the raw SPI byte stream for a chain of WS2812 LEDs (MOSI = DIN).

    ``brightness`` is 0..255 and scales every channel (WS2812 has no global
    brightness byte). ``order`` is the chip's wire order — WS2812/WS2812B is
    **grb**. Pure function: no hardware, fully unit-testable.
    """
    scale = max(0, min(255, brightness))
    out = bytearray()
    for r, g, b in pixels:
        chan = {"r": r & 0xFF, "g": g & 0xFF, "b": b & 0xFF}
        for c in order:
            out += _WS_TABLE[(chan[c] * scale) // 255]
    out += _WS_RESET
    return bytes(out)


@runtime_checkable
class StatusLed(Protocol):
    """The seam the runner drives: set a contract colour, release on shutdown."""

    color: str

    def set(self, color: str) -> None: ...
    def set_pixels(self, colors: list[str]) -> None: ...
    def close(self) -> None: ...


class NullStatusLed:
    """No-op LED for the VM / dev host (no SPI). Tracks state for tests."""

    def __init__(self, *, initial: str = OFF) -> None:
        self.color = initial
        self.pixels: list[str] = [initial]

    def set(self, color: str) -> None:
        self.color = color

    def set_pixels(self, colors: list[str]) -> None:
        self.pixels = list(colors)
        self.color = next((c for c in colors if c != OFF), OFF)

    def close(self) -> None:  # nothing to release
        self.color = OFF


class Apa102Led:
    """Paints the contract colour across an APA102 chain over an SPI handle.

    ``spi`` is anything with ``writebytes(list[int])`` and ``close()`` — the
    production handle is :class:`spidev.SpiDev`; tests inject a fake. SPI write
    failures are logged and swallowed: a dead status LED must never take down
    the voice loop.
    """

    def __init__(
        self,
        spi: object,
        *,
        count: int = DEFAULT_COUNT,
        brightness: int = DEFAULT_BRIGHTNESS,
        order: str = "bgr",
    ) -> None:
        self._spi = spi
        self._count = max(1, count)
        self._brightness = max(0, min(31, brightness))
        self._order = order
        self.color = OFF
        self._paint(OFF)  # known state at construction

    def set(self, color: str) -> None:
        """Paint ``color`` (a no-op if it's already showing, or unknown)."""
        if color == self.color or color not in RGB:
            return
        self.color = color
        self._paint(color)

    def set_pixels(self, colors: list[str]) -> None:
        """Paint a per-LED colour list — one entry per LED on the chain.

        Used for the 3 pipeline-phase LEDs (LISTEN / THINK / SPEAK). Unknown
        names and short/long lists are clamped to OFF / the chain length.
        """
        pixels = [RGB.get(c, RGB[OFF]) for c in colors[: self._count]]
        pixels += [RGB[OFF]] * (self._count - len(pixels))
        self.pixels = list(colors)
        self.color = next((c for c in colors if c != OFF), OFF)
        frame = apa102_frame(pixels, brightness=self._brightness, order=self._order)
        try:
            self._spi.writebytes(list(frame))  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 — never let the LED kill the loop
            log.warning("APA102 write failed (%s); status LED disabled", exc)

    def _paint(self, color: str) -> None:
        frame = apa102_frame(
            [RGB[color]] * self._count, brightness=self._brightness, order=self._order
        )
        try:
            self._spi.writebytes(list(frame))  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 — never let the LED kill the loop
            log.warning("APA102 write failed (%s); status LED disabled", exc)

    def close(self) -> None:
        """Blank the LEDs and release the SPI handle."""
        try:
            self._paint(OFF)
            self._spi.close()  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            log.debug("APA102 close failed (%s)", exc)


class Ws2812Led:
    """Paints the contract colour onto a WS2812/NeoPixel chain over SPI MOSI.

    Same seam as :class:`Apa102Led`. For a single indicator LED (``count == 1``)
    the 3-phase pipeline list is collapsed to the dominant (first non-off)
    colour, so one NeoPixel still shows the overall state. Write failures are
    logged and swallowed — a dead status LED must never take down the voice loop.
    """

    def __init__(
        self,
        spi: object,
        *,
        count: int = 1,
        brightness: int = WS2812_DEFAULT_BRIGHTNESS,
        order: str = "grb",
    ) -> None:
        self._spi = spi
        self._count = max(1, count)
        self._brightness = max(0, min(255, brightness))
        self._order = order
        self.color = OFF
        self.pixels: list[str] = [OFF]
        self._paint(OFF)

    def set(self, color: str) -> None:
        if color == self.color or color not in RGB:
            return
        self.color = color
        self.pixels = [color]
        self._write([RGB[color]] * self._count)

    def set_pixels(self, colors: list[str]) -> None:
        self.pixels = list(colors)
        self.color = next((c for c in colors if c != OFF), OFF)
        if self._count == 1:
            rgb = [RGB.get(self.color, RGB[OFF])]
        else:
            rgb = [RGB.get(c, RGB[OFF]) for c in colors[: self._count]]
            rgb += [RGB[OFF]] * (self._count - len(rgb))
        self._write(rgb)

    def _paint(self, color: str) -> None:
        self._write([RGB[color]] * self._count)

    def _write(self, rgb: list[tuple[int, int, int]]) -> None:
        frame = ws2812_frame(rgb, brightness=self._brightness, order=self._order)
        try:
            self._spi.writebytes(list(frame))  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 — never let the LED kill the loop
            log.warning("WS2812 write failed (%s); status LED disabled", exc)

    def close(self) -> None:
        try:
            self._paint(OFF)
            self._spi.close()  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            log.debug("WS2812 close failed (%s)", exc)


def open_status_led(
    *,
    led_type: str | None = None,
    bus: int | None = None,
    device: int | None = None,
    count: int | None = None,
    brightness: int | None = None,
    order: str | None = None,
    speed_hz: int | None = None,
) -> StatusLed:
    """Open the configured status LED over SPI, or return a :class:`NullStatusLed`.

    Two LED families share the SPI MOSI line: ``ws2812`` (a single NeoPixel is
    the Jabra-appliance recommendation — DIN→BCM10, one wire) and ``apa102`` (the
    legacy HAT chain — DIN→BCM10, CLK→BCM11). Type-appropriate defaults are
    chosen for count/brightness/order/speed; every field is env-overridable.

    Fail-soft: returns the no-op LED when disabled (``BLAZEN_LED=0`` or
    ``BLAZEN_LED_TYPE=none``), when ``spidev`` is missing, when
    ``/dev/spidev<bus>.<device>`` doesn't exist (SPI not enabled / the VM), or if
    the open fails for any reason. Env overrides: ``BLAZEN_LED`` (0 disables),
    ``BLAZEN_LED_TYPE`` (ws2812|apa102|none), ``BLAZEN_LED_BUS``,
    ``BLAZEN_LED_DEV``, ``BLAZEN_LED_COUNT``, ``BLAZEN_LED_BRIGHTNESS``,
    ``BLAZEN_LED_ORDER`` (channel order if the colours look wrong).
    """
    if os.environ.get("BLAZEN_LED", "1") == "0":
        return NullStatusLed()
    led_type = (led_type or os.environ.get("BLAZEN_LED_TYPE", "ws2812")).lower()
    if led_type in ("none", "off", "null"):
        return NullStatusLed()
    bus = int(os.environ.get("BLAZEN_LED_BUS", "0")) if bus is None else bus
    device = int(os.environ.get("BLAZEN_LED_DEV", "0")) if device is None else device
    ws = led_type == "ws2812"
    if count is None:
        count = int(os.environ.get("BLAZEN_LED_COUNT", "1" if ws else str(DEFAULT_COUNT)))
    if brightness is None:
        brightness = int(os.environ.get(
            "BLAZEN_LED_BRIGHTNESS", str(WS2812_DEFAULT_BRIGHTNESS if ws else DEFAULT_BRIGHTNESS)))
    if order is None:
        order = os.environ.get("BLAZEN_LED_ORDER", "grb" if ws else "bgr")
    if speed_hz is None:
        speed_hz = WS2812_SPEED_HZ if ws else DEFAULT_SPEED_HZ

    if not Path(f"/dev/spidev{bus}.{device}").exists():
        log.info("no /dev/spidev%d.%d — status LED runs headless (led.json only)", bus, device)
        return NullStatusLed()
    try:
        import spidev  # noqa: PLC0415 — optional, hardware-only dependency
    except Exception as exc:  # noqa: BLE001
        log.info("spidev unavailable (%s); status LED runs headless", exc)
        return NullStatusLed()
    try:
        spi = spidev.SpiDev()
        spi.open(bus, device)
        spi.max_speed_hz = speed_hz
        spi.mode = 0
    except Exception as exc:  # noqa: BLE001
        log.warning("could not open SPI%d.%d (%s); status LED disabled", bus, device, exc)
        return NullStatusLed()
    log.info("%s status LED on SPI%d.%d (%d LEDs, %s, %d Hz)",
             "WS2812" if ws else "APA102", bus, device, count, order, speed_hz)
    if ws:
        return Ws2812Led(spi, count=count, brightness=brightness, order=order)
    return Apa102Led(spi, count=count, brightness=brightness, order=order)


def _diag(hold_s: float = 0.6) -> None:
    """Visual smoke test: cycle every contract colour on the HAT, then blank.

    ``python -m blazend.domains.systems.adapters.rpi5.led_hw`` — the headless way to confirm the LEDs (and the
    channel order) without booting the whole voice pipeline.
    """
    import time  # noqa: PLC0415 — diagnostic-only

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    led = open_status_led()
    log.info("status LED: %s", type(led).__name__)
    for color in (GREEN, BLUE, MAGENTA, YELLOW, RED, OFF):
        log.info("  %s", color)
        led.set(color)
        time.sleep(hold_s)
    led.close()


__all__ = [
    "RGB",
    "StatusLed",
    "NullStatusLed",
    "Apa102Led",
    "apa102_frame",
    "open_status_led",
]


if __name__ == "__main__":  # pragma: no cover — hardware diagnostic
    _diag()
