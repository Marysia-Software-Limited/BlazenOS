# 20 — Common Linux installer (`installer/`)

The full Jessica voice pipeline — audio-in → wake/ASR → NLU → brain → TTS →
audio-out, orchestrator, health — running natively on a generic Linux box, no
Pi image involved. First target and reference install: **paul** (Arch x86_64,
RTX 3090). Decision 2026-08-23.

```bash
installer/install.sh                       # desktop mode, CUDA profile
installer/install.sh --dry-run             # print every action, touch nothing
installer/install.sh --mode appliance      # Pi-like system layout on a dedicated box
installer/install.sh --profile cpu         # appliance model set (no GPU)
installer/install.sh --uninstall           # manifest-based removal
```

## Platforms

`lib/distro-{arch,debian,fedora}.sh` carry the package-manager layer (pacman /
apt / dnf); `lib/distro-macos.sh` is a deliberate stub marking the seam for the
future macOS port (launchd + CoreAudio + Homebrew go there and nowhere else).
`--yes` makes every step non-interactive — the intended path for QEMU-based
installer testing.

## Modes

| | desktop (default) | appliance |
|---|---|---|
| units | systemd **user** (`~/.config/systemd/user`, linger) | system units + `blazen` user |
| layout | `~/.local/share/blazen` + `~/.config/blazen` | `/usr/lib/blazen` + `/etc/blazen` (Pi layout) |
| config layering | `BLAZEN_CONFIG_ROOT=defaults:site` (pathsep list) | same two roots as the Pi |
| root needed | package install only | yes |
| half-duplex hand-off | `BLAZEN_SYSTEMCTL="systemctl --user"` | sudoers rule (as on the Pi) |

## Audio: Jabra preferred, ALSA fallback — decided at every start

The oneshot `blazend-env.service` runs `blazen-audio-env` before every
audio-consuming unit and writes `$XDG_RUNTIME_DIR/blazen/audio.env`:

- **Jabra SPEAK 410 present** (`0b0e:0412` in `/proc/asound`): direct `plughw`
  capture on its card, the forced-48 kHz `jabra_out` playback PCM (via
  `ALSA_CONFIG_PATH`, never system-wide — the desktop default is untouched),
  the measured appliance VAD thresholds, amixer volume on the card. A
  user-scope wireplumber rule releases **only** the Jabra from PipeWire
  (direct hw capture dies if PipeWire holds the card). The detector also maxes
  the Jabra **capture** gain (control `Headset`, ships at 5/7 ≈ 71%;
  2026-08-27) — more mic signal for the wake model, the ASR RMS floor and
  whisper alike. **Playback volume is a softvol** (2026-08-29): desktop
  kernels expose no hardware playback volume on the SPEAK 410 (its `PCM`
  control is a bare mute switch — voice volume commands confirmed percentages
  while nothing changed), so `jabra_out` routes through an ALSA softvol whose
  `Jessica` control (created at first playback, default 85%) is what
  `BLAZEN_MIXER_CONTROL` now points at. Media (radio/music/books/beeps) obey
  it; TTS speech via cpal deliberately bypasses it — her voice stays at a
  constant level.
- **No Jabra**: ALSA `default` through pipewire-alsa for capture and playback,
  adaptive VAD, no dedicated mixer (voice volume commands no-op).

Plug the Jabra in → `systemctl --user restart blazend.target` → profile
switches. No reinstall, no config edit.

The wake threshold is per-site too (2026-08-27): the unit defaults
`BLAZEN_WAKE_THRESHOLD=0.8`; set it in the site `blazen.env` when the room/mic
needs a laxer or stricter wake (paul runs `0.65`). `blazend-wake` logs
`wake near-miss` for scores just under the threshold, so calibrate from the
journal, not by feel.

**Headless rigs (no logind seat, e.g. SSH-only):** logind grants device ACLs
only to an active seat session, so on a headless machine neither PipeWire nor
the user units can open any sound card — PipeWire showing only the null sink
is expected there. Desktop-mode install therefore drops
`/etc/udev/rules.d/89-blazen-jabra.rules` (rendered from
`installer/templates/udev/89-blazen-jabra.rules.in`), which sets
`OWNER=<installing user>` on the **Jabra card only** (usb `0b0e:0410`); every
other card stays `root:audio` and regular desktop audio is untouched. This is
the one desktop-mode step needing root beyond package installs; without sudo
it degrades to a warning. `--uninstall` removes the rule (it is root-owned,
so it lives outside the unprivileged install manifest).

## GPU profile (`--profile cuda`)

- **ASR**: faster-whisper **large-v3-turbo** on CUDA fp16 (site `asr.yaml`).
  Measured on paul: 0.49 s for a 4 s Polish utterance (the Pi's whisper-small
  needs ~11 s). CUDA libs come from pip nvidia wheels (`nvidia-cublas-cu12`,
  `nvidia-cudnn-cu12`) + `LD_LIBRARY_PATH` in `blazen.env` — distro-agnostic,
  no system cuDNN. Requires only the NVIDIA driver; without `nvidia-smi` the
  installer downgrades to `--profile cpu` with a warning.
- **LLM**: local **Ollama** with `SpeakLeash/bielik-11b-v2.3-instruct:Q8_0`
  (site `llm.yaml` routes every task to `ollama-11b`; self-only `mesh.yaml`
  supplies the URL). No llama.cpp in the venv on this profile.
- **TTS**: Piper floor (as the Pi); `--with-xtts` points at the existing
  `blazen-xtts.service` on :8091.
- `ORT_DYLIB_PATH` is discovered at install time (`find` over the venv), never
  pinned to a Python version.

## What stays identical to the Pi

The Python tree, Rust binaries, configs, intents (all 82), wake model and the
IPC contract are the same artifacts. All portability seams default to the
appliance literals (`3e65cae`, `cddef41`) so the Pi image is behavior-
identical; the pi-gen build (`rpi5/stage-blazen`) is untouched.

## Verification checklist

1. `installer/install.sh --dry-run` prints the full action list.
2. `systemctl --user start blazend.target` → 9 services active
   (`blazend-env` shows the chosen audio profile in its journal).
3. ASR journal shows `model=large-v3-turbo`; NLU `intents=82`; wake
   `model loaded`.
4. Voice roundtrip needs real speaker+mic hardware (the Jabra): say
   "dżesika", command, spoken reply. On a headset/HDMI-audio desktop the
   pipeline runs but the room can't reach the mic.

## Known limits

- The acoustic path on a bare desktop depends on what PipeWire's `default`
  points at — headsets work for the wearer, HDMI audio reaches no mic.
- `blazend-bootstrap`, `blazend-button`, the USB gadget and LED units are
  appliance-only and not installed.
- macOS: stubbed, not implemented.
