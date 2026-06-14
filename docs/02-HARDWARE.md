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

| Signal      | Meaning                                       |
|-------------|-----------------------------------------------|
| LED off     | System asleep / not listening.                |
| LED green   | Listening for wake word.                      |
| LED blue    | Wake detected, capturing utterance.           |
| LED magenta | Processing (ASR/LLM).                         |
| LED yellow  | Reprompt — please repeat.                     |
| LED red     | Error — recovery mode (SSH already on).       |
| Short beep  | Wake confirmed (configurable; default off).   |
| Long beep   | Falling into SSH recovery mode.               |
| Voice tone  | Optional persona tone marking state changes.  |

The LED protocol is defined in [`07-CONFIGURATION.md`](07-CONFIGURATION.md).
The orchestrator runs an **LED simulator** that derives the colour from the
live event stream and writes it to `/run/blazen/led.json`
(off→asleep, green→listening, blue→capturing, magenta→processing,
yellow→reprompt, red→error) — see `blazend/led.py`. On real hardware an
**APA102 SPI driver** paints the same colour across the HAT's 3 on-board RGB
LEDs (identical colour contract); in the VM / on the dev host `led.json` is
the status surface (and what Tier-3 scenarios assert on).

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
