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


def open_status_led(
    *,
    bus: int | None = None,
    device: int | None = None,
    count: int | None = None,
    brightness: int | None = None,
    order: str | None = None,
    speed_hz: int = DEFAULT_SPEED_HZ,
) -> StatusLed:
    """Open the HAT's APA102 chain, or return a :class:`NullStatusLed`.

    Fail-soft: returns the no-op LED when disabled (``BLAZEN_LED=0``), when
    ``spidev`` is missing, when ``/dev/spidev<bus>.<device>`` doesn't exist
    (SPI not enabled, or running in the VM), or if the open fails for any
    reason. Env overrides: ``BLAZEN_LED`` (0 disables), ``BLAZEN_LED_BUS``,
    ``BLAZEN_LED_DEV``, ``BLAZEN_LED_COUNT``, ``BLAZEN_LED_BRIGHTNESS``,
    ``BLAZEN_LED_ORDER`` (channel order if a HAT shows the wrong colours).
    """
    if os.environ.get("BLAZEN_LED", "1") == "0":
        return NullStatusLed()
    bus = int(os.environ.get("BLAZEN_LED_BUS", "0")) if bus is None else bus
    device = int(os.environ.get("BLAZEN_LED_DEV", "0")) if device is None else device
    count = int(os.environ.get("BLAZEN_LED_COUNT", str(DEFAULT_COUNT))) if count is None else count
    if brightness is None:
        brightness = int(os.environ.get("BLAZEN_LED_BRIGHTNESS", str(DEFAULT_BRIGHTNESS)))
    if order is None:
        order = os.environ.get("BLAZEN_LED_ORDER", "bgr")

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
    log.info("APA102 status LED on SPI%d.%d (%d LEDs, %s)", bus, device, count, order)
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
