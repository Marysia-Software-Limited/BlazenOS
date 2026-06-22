"""Tier 0 — APA102 hardware status-LED driver (the HAT's status surface).

Pure-function frame building plus the fail-soft factory, exercised with a fake
SPI handle so no real ``/dev/spidev`` is touched (the dev rig is a real Pi 5
where SPI is enabled — these tests must stay hardware-free).
"""
from __future__ import annotations

from blazend import led, led_hw
from blazend.led_hw import (
    Apa102Led,
    NullStatusLed,
    apa102_frame,
    open_status_led,
)


class FakeSpi:
    """Records every writebytes() payload instead of touching the bus."""

    def __init__(self) -> None:
        self.writes: list[list[int]] = []
        self.closed = False
        self.max_speed_hz = 0
        self.mode = -1

    def writebytes(self, data: list[int]) -> None:
        self.writes.append(list(data))

    def close(self) -> None:
        self.closed = True


def test_frame_layout_start_brightness_order_and_end():
    # One red LED at full brightness 31.
    frame = apa102_frame([(255, 0, 0)], brightness=31)
    assert frame[:4] == b"\x00\x00\x00\x00"          # start frame
    assert frame[4] == 0xFF                            # 0b111 + 31
    assert frame[5:8] == b"\x00\x00\xff"              # wire order is B, G, R
    assert frame[8:] == b"\x00"                        # end frame = ceil(1/16)


def test_frame_brightness_is_clamped_to_five_bits():
    assert apa102_frame([(0, 0, 0)], brightness=999)[4] == 0xFF  # 0xE0 | 0x1F
    assert apa102_frame([(0, 0, 0)], brightness=-5)[4] == 0xE0


def test_frame_three_leds_repeats_pixel_and_grows_end_frame():
    frame = apa102_frame([(0, 255, 0)] * 3, brightness=8)
    # start(4) + 3*led(4) + end(ceil(3/16)=1) = 17 bytes
    assert len(frame) == 17
    bright = 0xE0 | 8
    assert frame[4] == bright and frame[8] == bright and frame[12] == bright
    assert frame[5:8] == b"\x00\xff\x00"  # green → B=0, G=255, R=0


def test_frame_order_override_rearranges_channels():
    # order="rgb" emits R, G, B after the brightness byte (e.g. a clone HAT).
    assert apa102_frame([(255, 0, 0)], brightness=31, order="rgb")[5:8] == b"\xff\x00\x00"


def test_apa102_honours_order_override():
    spi = FakeSpi()
    leds = Apa102Led(spi, count=1, order="grb")
    leds.set(led.RED)
    # grb → G, R, B; red = (255, 0, 0) → 0x00, 0xFF, 0x00
    assert spi.writes[-1][5:8] == [0x00, 0xFF, 0x00]


def test_rgb_map_covers_every_contract_colour():
    for color in (led.OFF, led.GREEN, led.BLUE, led.MAGENTA, led.YELLOW, led.RED):
        assert color in led_hw.RGB


def test_null_led_is_a_no_op_but_tracks_colour():
    null = NullStatusLed()
    assert null.color == led.OFF
    null.set(led.GREEN)
    assert null.color == led.GREEN
    null.close()
    assert null.color == led.OFF


def test_apa102_paints_on_construction_and_dedupes():
    spi = FakeSpi()
    leds = Apa102Led(spi, count=3, brightness=8)
    assert leds.color == led.OFF
    assert len(spi.writes) == 1                 # painted OFF at construction

    leds.set(led.GREEN)
    assert leds.color == led.GREEN and len(spi.writes) == 2
    leds.set(led.GREEN)                          # same colour → no extra write
    assert len(spi.writes) == 2
    leds.set("not-a-colour")                     # unknown → ignored
    assert leds.color == led.GREEN and len(spi.writes) == 2


def test_apa102_close_blanks_and_releases():
    spi = FakeSpi()
    leds = Apa102Led(spi, count=1)
    leds.set(led.RED)
    leds.close()
    assert spi.closed
    # Last write before close is the OFF (blank) frame.
    assert spi.writes[-1] == list(apa102_frame([led_hw.RGB[led.OFF]], brightness=8))


def test_apa102_swallows_spi_write_errors():
    class BoomSpi(FakeSpi):
        def writebytes(self, data: list[int]) -> None:
            raise OSError("SPI gone")

    # Construction paints OFF → would raise; must be swallowed (LED never kills
    # the loop). Setting a colour must also stay silent.
    leds = Apa102Led(BoomSpi(), count=1)
    leds.set(led.BLUE)
    assert leds.color == led.BLUE


def test_open_status_led_disabled_by_env(monkeypatch):
    monkeypatch.setenv("BLAZEN_LED", "0")
    assert isinstance(open_status_led(), NullStatusLed)


def test_open_status_led_no_device_returns_null(monkeypatch):
    monkeypatch.delenv("BLAZEN_LED", raising=False)
    # Point at a bus/device that cannot exist so the path check fails fast,
    # regardless of whether the host actually has SPI enabled.
    assert isinstance(open_status_led(bus=99, device=99), NullStatusLed)
