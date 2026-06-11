# 03 — Software stack

## Base operating system

- **Raspberry Pi OS Lite 64-bit (Trixie)** — Debian 13. Pinned by
  pi-gen release tag (`2026-04-13-raspios-trixie-arm64` at M1). The
  Bookworm line is still receiving security updates but pi-gen master
  has moved to Trixie; we follow.
- **Kernel:** the stock Raspberry Pi Foundation kernel. We do not maintain
  a custom kernel; we ship a kernel boot config plus a `cmdline.txt` snippet.
- **Init:** `systemd`. All `blazend-*` components are systemd units.
- **Filesystem:** `ext4`, optional `overlayfs` to make `/` read-only at
  runtime (toggleable via voice + SSH; default ON in release builds, OFF
  in dev images so we can iterate without rebuild loops).

## Image build pipeline

We use [`pi-gen`](https://github.com/RPi-Distro/pi-gen) as the upstream
image builder, with our own "stage" appended:

```
pi-gen/
  stage0..stage2     # upstream — minimal Lite system
  stage-blazen/      # ours: installs blazend-* + models + configs + systemd units
```

Build is driven by `scripts/build-image.sh`, which:

1. Clones a pinned `pi-gen` revision into `build/pi-gen-src/`.
2. Drops our `stage-blazen/` next to it.
3. Sets `IMG_NAME=$BLAZEN_IMAGE_NAME` and runs `pi-gen` in Docker.
4. Outputs `vm-images/<name>-<version>.{img,qcow2}` depending on format.

We do not vendor `pi-gen`; pinning is by SHA.

## Required runtime packages (pinned)

| Package                  | Reason                                  |
|--------------------------|-----------------------------------------|
| `alsa-utils`             | `aplay`, `arecord` baseline.            |
| `pipewire`, `wireplumber`| Audio router; needed for AEC + routing. |
| `libasound2`, `libasound2-dev` | ALSA libs (runtime + headers for `cpal`). |
| `python3` (>=3.11)       | Orchestrator runtime.                   |
| `python3-venv`           | Per-component venvs.                    |
| `build-essential`        | For wheels needing compilation.         |
| `cmake`, `ninja-build`   | llama.cpp build.                        |
| `git`                    | Submodules + recovery utilities.        |
| `openssh-server`         | Disabled by default, enabled in recovery. |
| `ufw`                    | Firewall (deny incoming by default).    |
| `i2c-tools`, `spi-tools` | HAT bring-up.                           |
| `dnsmasq`                | Optional AP-mode fallback for setup.    |
| `hailort`, `hailo-pci`   | Optional. Only installed when the user opts into the accelerator path; see [`12-ML-ACCELERATOR.md`](12-ML-ACCELERATOR.md). |

The **Rust runtime needs no extra Pi packages**: each `blazend-*` Rust
binary is statically linked (except `libc` + `libasound2`) and lands
under `/usr/lib/blazen/bin/` at image-build time. The Rust toolchain is
**not** installed on the Pi — cross-compilation happens on the
developer machine via `make rust-aarch64`.

All pins live in `configs/system.yaml: packages`.

## Components we build (`blazend-*` units)

The implementation language of each unit is fixed by
[`docs/14-RUST-PYTHON-SPLIT.md`](14-RUST-PYTHON-SPLIT.md).

| Unit                       | Lang   | Implementation                          | RAM (Pi 5)   |
|----------------------------|--------|-----------------------------------------|-------------:|
| `blazend-orchestrator`     | Python | asyncio + pydantic                      | ~40 MB       |
| `blazend-audio-in`         | Rust   | `cpal` + ring buffer + WebRTC AEC FFI   | ~30 MB       |
| `blazend-wake`             | Rust   | `ort` (ONNX Runtime) — N models in parallel | ~120 MB  |
| `blazend-asr`              | Python | `faster-whisper` (CTranslate2)          | 400–1500 MB  |
| `blazend-brain`            | Python | `llama-cpp-python` (CPU) **or** HailoRT | 1.8–3 GB CPU / 1.8 GB Hailo |
| `blazend-tts`              | Rust   | `piper-rs` wrapping Piper voices        | ~150 MB      |
| `blazend-audio-out`        | Rust   | `cpal` + `rodio` mixer                  | ~25 MB       |
| `blazend-health`           | Rust   | `tokio` watchdog + sd-notify            | ~10 MB       |
| `blazend-bootstrap`        | Python | stdlib (first-boot pairing)             | ~20 MB (transient) |
| `blazend-ssh-recovery`     | Shell  | systemd timer                            | ~5 MB        |

Total resident set on Pi 5 **16 GB** with multilingual `medium` ASR +
Qwen 2.5 3B Q4 + both Piper voices warm: **~4.5 GB** on CPU path,
**~3.5 GB** on Hailo path, leaving 11–12 GB free for kernel cache,
conversation context, plugins, and the optional jump to Qwen 2.5 7B Q4
(~4.5 GB additional). See [`docs/12-ML-ACCELERATOR.md`](12-ML-ACCELERATOR.md)
and [`docs/05-MODELS.md`](05-MODELS.md).

## Why not Wyoming / Rhasspy?

[Rhasspy 3 (Wyoming)](https://github.com/rhasspy/rhasspy3) and its
satellite/server split is a great inspiration and shares Piper + Whisper.
We **diverge** because:

1. We do not want a server: blazen is a single-node, single-user appliance.
2. We want LLM-driven conversation as a first-class stage, not a plugin.
3. The IPC contract here is tailored to our latency budget — fewer hops.

That said, individual Wyoming-compatible satellites can be added as
plugins in M8+; the `blazend-orchestrator` IPC contract is documented so
adapters are mechanical.

## Why Python for orchestration (and Rust for the hot path)?

The decision splits cleanly along latency sensitivity:

- **Python is great** at composing heterogeneous APIs at ~Hz cadence
  (orchestrator, ASR API, LLM API, intent routing) where the underlying
  work is C/C++ anyway. We benefit from rapid iteration and a rich ML
  ecosystem (faster-whisper, llama-cpp-python, HailoRT).
- **Rust is necessary** on the audio + wake + TTS + watchdog path. These
  run at kHz cadence (audio frames every 20 ms), can't tolerate the GIL,
  and must run for weeks without crashing. `cpal`, `ort`, `tokio`, and
  `serde_json` give us small static binaries with predictable jitter.

The IPC contract is the boundary; nothing crosses the FFI line inside
a component. See [`docs/14-RUST-PYTHON-SPLIT.md`](14-RUST-PYTHON-SPLIT.md).

## What is NOT in scope

- A GUI. There is no desktop, no LXDE, no Wayland session.
- A web admin UI. Some users will want one — we will revisit at M9.
- Multi-user accounts. Single-user appliance only.
- OTA updates over the air. Updates flow through a fresh image flash or
  `apt`-based component updates via SSH recovery (M7+).
- A third implementation language. Python + Rust only, per
  [`docs/14-RUST-PYTHON-SPLIT.md`](14-RUST-PYTHON-SPLIT.md).
