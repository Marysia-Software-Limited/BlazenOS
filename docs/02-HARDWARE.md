# 02 — Hardware

## Reference platform

> **Decision (2026-06-11):** **Raspberry Pi 5, 16 GB** is the **reference**
> platform for M1..M10. All performance tables, latency budgets, model
> defaults, and hardware accessories are pinned to it. Pi 5 8 GB is
> a supported secondary target (with smaller LLM/ASR variants). Pi 5 4 GB
> is best-effort. Older Pi 4/3 are out of scope.

| Platform              | RAM   | Status              | LLM headroom |
|-----------------------|-------|---------------------|-------------:|
| Raspberry Pi 5        | 16 GB | **Reference**       | ~10 GB GGUF  |
| Raspberry Pi 5        | 8 GB  | Supported           | ~4 GB GGUF   |
| Raspberry Pi 5        | 4 GB  | Best-effort (no CI) | ~2 GB GGUF   |
| Raspberry Pi 4 / 3    | any   | Out of scope        | n/a          |

The 16 GB variant lets us keep `medium` ASR + a 7 B-class LLM + both
Piper voices warm simultaneously with ample headroom for KV cache,
plugins, and a healthy page cache.

## Optional ML accelerator

The reference build runs the LLM on the Pi 5 CPU. An accelerator is
**optional** — adding one only speeds up the LLM (and optionally ASR)
without changing observable behaviour.

Recommended: **Raspberry Pi AI HAT+ 26T (Hailo-8, 26 TOPS)** for the
balanced everyday build, or **Raspberry Pi AI HAT+ 10H (Hailo-10H,
40 TOPS)** for the snappy / larger-LLM build. Both connect via the Pi 5
PCIe FFC ribbon. See [`12-ML-ACCELERATOR.md`](12-ML-ACCELERATOR.md) for
the full integration, model preparation pipeline, and budget tables.

> The CPU path is the **contract**. Every feature must work without an
> accelerator. The accelerator is a strict performance improvement.

## Storage

- microSD: **64 GB Class 10 / A2** minimum (32 GB possible but tight after
  models). Recommended: SanDisk Extreme 64 GB or Samsung Pro Endurance 64 GB.
- USB SSD via USB 3.0 is supported and **strongly recommended** for soak
  testing. Pi 5 NVMe HAT is the future-proof option.
- Image layout:

  ```
  [ boot (FAT32, 512 MB) ][ root (ext4, RW, ~6 GB) ][ data (ext4, RW, rest) ]
  ```
  `root` is overlayfs read-only by default (toggleable from voice or SSH).
  `data` holds models, logs, and conversation cache (purgeable on demand).

## Audio I/O

> **Decision (2026-06-15):** The **reference audio interface** is the
> **Seeed ReSpeaker 2-Mics Pi HAT V2.0** (Seeed SKU 107100001). It is the
> primary target for mic in, speaker out, and status LEDs; everything else
> below is a fallback for dev hosts and stress testing. V2.0 (not V1) is
> required for **Raspberry Pi 5 support** — it swaps the V1 WM8960 codec for
> the **TI TLV320AIC3104**.

### Microphone (priority order)

1. **ReSpeaker 2-Mics Pi HAT V2.0 — the reference interface.** Snaps onto
   the Pi 5 40-pin header (no soldering). Key facts that drive our config:
   - **Codec:** TI **TLV320AIC3104** over **I2S** (+ I2C control). Capture
     is **2-channel** (the two analog mics); we downmix to mono 16 kHz for
     ASR. Sample-rate range 8–96 kHz.
   - **Status LEDs:** **3× APA102 RGB** on **SPI** — this is the real
     surface the LED simulator (`blazend/led.py`) drives on hardware.
   - **Button:** on-board user button on **GPIO17** (optional physical
     wake / push-to-talk).
   - **Out:** on-board **3.5 mm jack** + JST mono speaker connector.
   - **Device-tree overlay:** `respeaker-2mic-v2_0.dtbo`, built from
     [`seeed-linux-dtoverlays`](https://github.com/Seeed-Studio/seeed-linux-dtoverlays)
     (set `dtoverlay=respeaker-2mic-v2_0` in `config.txt`). This is the M8
     bring-up step that exposes the ALSA capture/playback devices.
   - We use the board as a **plain 2-mic codec + LEDs + button** — not
     Seeed's bundled VAD/DOA/KWS SDK; our pipeline owns those stages.
2. **ReSpeaker 4-Mic Linear Array** — better beamforming for noisy rooms;
   used for the M9 acoustic-stress tests.
3. **Any USB mic with ALSA support** — e.g., Jabra Speak 510, Anker
   PowerConf S3, Blue Snowball. Easier to source but no status LEDs.

> **Live-rig finding (2026-06-22, `jessica` 8 GB).** The dev rig runs an
> older **WM8960**-codec HAT on the **mainline `wm8960-soundcard`** overlay
> (not the V2 `respeaker-2mic-v2_0` overlay above). On it, **mic capture is
> marginal**: it captures almost entirely **sub-bass electrical rumble**
> (~98 % of energy <100 Hz, dominant ~0–23 Hz) even in silence — the symptom
> of an unbiased/floating analog input (MICBIAS not powered by this overlay;
> verified: forcing MICBIAS via `i2cset` didn't revive it, and all of
> LINPUT1/2/3 read pure DC). Real speech is only intelligible in a **thin
> gain window** (`amixer -c <card> set Capture 45` + the `Boost Mixer
> LINPUT1/RINPUT1` DAPM switches on **during** an active capture), spoken
> **short, close, moderate volume** — then Whisper/wake work (wake scored
> 0.99 once tuned). **Speaker/TTS output on the HAT is unaffected and works.**
> Conclusion: the WM8960 HAT mic is **not reliable for hands-free voice**;
> the robust fix is a **USB mic** (`blazend-audio-in`/cpal auto-selects it,
> keep the HAT for speaker out) or the V2 HAT + its overlay. See the
> `jessica-live-rig` engineering note.

### Speaker

- For dev: any 3.5 mm or USB speaker.
- On the reference build: the HAT's **3.5 mm jack** (or JST mono connector)
  → a small powered speaker.

### Why this HAT

The 2-Mics Pi HAT V2.0 is a **plain stereo codec** (no on-board beamforming
DSP — that is the 4-Mic Array's job). End-of-utterance detection, echo
cancellation, and noise suppression all run **on the CPU** (`silero-vad` +
WebRTC APM) across M1..M8; the 4-Mic array is only introduced for the M9
acoustic-stress work. The HAT earns its place by giving us two phase-aligned
mics, the 3 status LEDs, and a button in one solder-free board.

## Status feedback without a screen

Because there is no monitor, the system communicates state out-of-band:

**Per-phase LEDs.** The HAT's 3 APA102 RGB LEDs each track one stage of a
turn, so you can watch it flow down the board (`blazend.domains.systems.adapters.rpi5.led.PipelineLeds`,
driven by the orchestrator over SPI):

| LED | Phase  | off | active colours |
|-----|--------|-----|----------------|
| 0   | LISTEN | asleep | **green** = listening for wake · **blue** = capturing speech |
| 1   | THINK  | idle | **magenta** = ASR / NLU / Bielik thinking |
| 2   | SPEAK  | idle | **blue** = synthesising / speaking (TTS) |

Faults override all three: **yellow** = degraded / reprompt, **red** = error /
recovery mode (SSH already on). The identical contract is mirrored to
`/run/blazen/led.json` (`leds: [...]`) for the headless/VM path.

**Jabra appliance status LED (no HAT).** On the Jabra-only build there is no HAT,
so a **single WS2812/NeoPixel** carries the state — it collapses the 3-phase
contract to the dominant colour (idle=off, listening=green, capturing=blue,
thinking=magenta, speaking=blue, error=red). Driven from **SPI0 MOSI** by
`led_hw.Ws2812Led` (3 SPI bits per WS2812 bit at 2.4 MHz — the reliable Pi 5
method). Wiring — three jumpers, no HAT:

| NeoPixel pin | Pi 5 header |
|--------------|-------------|
| DIN (data)   | **GPIO10 / MOSI — pin 19** |
| 5V / VCC     | 5V — pin 2 or 4 |
| GND          | GND — pin 6 |

Enabled by `dtparam=spi=on` (set in `stage-blazen`); the orchestrator service
sets `BLAZEN_LED_TYPE=ws2812` / `BLAZEN_LED_COUNT=1`. Fail-soft: no LED wired or
SPI off → `led.json` stays the only status surface. For a legacy APA102 chain
set `BLAZEN_LED_TYPE=apa102` (adds CLK on GPIO11 / pin 23); `=none` disables.
Other cues:

| Signal      | Meaning                                       |
|-------------|-----------------------------------------------|
| Short beep  | Wake confirmed (configurable; default off).   |
| Long beep   | Falling into SSH recovery mode.               |
| Voice tone  | Optional persona tone marking state changes.  |

The LED protocol is defined in [`07-CONFIGURATION.md`](07-CONFIGURATION.md).
The orchestrator runs an **LED simulator** that derives the colour from the
live event stream and writes it to `/run/blazen/led.json`
(off→asleep, green→listening, blue→capturing, magenta→processing,
yellow→reprompt, red→error) — see `blazend/led.py`, the single colour contract.

On real hardware the **APA102 SPI driver** (`blazend/led_hw.py`) paints the same
contract colour across the HAT's 3 on-board RGB LEDs over **SPI0**
(`/dev/spidev0.0`, BCM10 MOSI / BCM11 SCLK; needs `dtparam=spi=on`). It is wired
into the hands-free voice runner (`blazend.domains.voice_input.adapters.rpi5.voice`), so the LEDs track the live
pipeline state — green listening → blue capturing → magenta processing → green.
The driver is **fail-soft**: with no SPI device (the VM / a dev host) it degrades
to a no-op and `led.json` stays the status surface (and what Tier-3 scenarios
assert on) — the headless/CPU path remains the contract. Verify the LEDs (and
channel order) on a Pi with `python -m blazend.domains.systems.adapters.rpi5.led_hw`, which cycles every
colour. Tuning knobs (`BLAZEN_LED*`) are in
[`07-CONFIGURATION.md`](07-CONFIGURATION.md).

## Power

- **27 W USB-C PD** (official Raspberry Pi PSU) — required.
- LLM inference draws sustained 6–7 W on CPU; +3–4 W when the Hailo
  accelerator is engaged.
- Optional UPS HAT (e.g., UPS HAT C from Waveshare) for the "always-on"
  appliance use case.
- Active cooler required when the accelerator is in use above 25°C
  ambient.

## Networking

- Ethernet preferred for dev / soak testing.
- WiFi 802.11ac on-board. Headless first-boot configuration via
  `wpa_supplicant.conf` injected into the boot partition (see
  [`06-SSH-BOOTSTRAP.md`](06-SSH-BOOTSTRAP.md)).
- No Bluetooth use cases planned for M1..M8.

## GPIO + PCIe usage

| Pin/Bus | Use                                              |
|---------|--------------------------------------------------|
| BCM2    | I2C SDA (TLV320AIC3104 codec control, HAT EEPROM, optional UPS) |
| BCM3    | I2C SCL                                          |
| BCM18   | I2S BCLK (ReSpeaker 2-Mics HAT V2.0)             |
| BCM19   | I2S LRCK                                         |
| BCM20   | I2S DIN (mic capture)                            |
| BCM21   | I2S DOUT (speaker playback)                      |
| BCM10   | SPI MOSI — 3× APA102 status LEDs (HAT)           |
| BCM11   | SPI SCLK — APA102 clock                          |
| BCM17   | HAT user button (optional physical wake / PTT)   |
| PCIe FFC | Pi 5 single PCIe Gen 3 ×1 — used by the optional Hailo AI HAT+ / AI Kit. Mutually exclusive with NVMe HAT. |

If both an NVMe SSD HAT and an AI HAT+ are desired, route through a
PCIe switch HAT (Pineboards HatDrive Bottom + AI HAT+ stack is the
tested combination as of M9 planning).

A physical wake button is optional and disabled by default; useful for
tradeshow demos in noisy environments.
