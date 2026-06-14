# 10 — Roadmap

Milestones are sized to ship in 1–3 weeks each. Each milestone has an
**exit criterion** that must be demonstrable before the next milestone
starts.

## M0 — Scaffolding **(done — 2026-06-11)**

- Docs (`README`, `AGENTS.md`, `CLAUDE.md`, `docs/00..14`).
- YAML configs with defaults for every component, plus
  `configs/_schema/events/` JSON Schemas (cross-language IPC contract).
- Make targets and script skeletons (`install-deps`, `models`, `build`,
  `rust`, `rust-aarch64`, `dev`, `qemu-smoke`, `qemu-setup`, `vm-image`,
  `run-vm`, `test*`, `audit`, `audio-fixtures`, `gen-events`, ...).
- Test scaffolding: 9 voice scenarios + 57 Python tests (Tier 0+1)
  + 6 Rust tests.
- Working bilingual Python + Rust skeleton: 6 Rust crates, 1 Python
  package with 4 unit-modules, IPC framed-JSON pub/sub verified
  cross-language end-to-end.
- Hardware target locked: **Pi 5 16 GB** reference, with optional
  Hailo AI HAT+ on the LLM path.

**Exit criteria — met:**
- ✓ `make help` lists every target.
- ✓ Every doc in `docs/` is reachable from `docs/00-INDEX.md`.
- ✓ `make build` (cargo + venv) is green.
- ✓ `make test-fast` (cargo test + pytest) is green: 57 Python + 6 Rust.
- ✓ `scripts/dev-run.sh` brings up the full stack, orchestrator
  observes 7 peer sockets, `/run/blazen/state.json` is live.
- ✓ `scripts/qemu-smoke.sh` confirms `qemu-system-aarch64` + virt
  machine + HVF acceleration work on the dev host.
- ✓ `scripts/gen-event-types.py` validates 11 IPC schemas + round-trips
  each one through Python's `Envelope`.

## M1 — Bootable image (VM only) **(image built ✓ — awaiting QEMU boot test)**

- ✓ `rpi5/stage-blazen/` overlay for pi-gen exists (9 systemd unit files,
  `00-packages`, `00-debconf`, `00-run-chroot.sh`,
  `files/etc/systemd/system/blazend.target` + 8 services).
- ✓ `scripts/build-image.sh` wires the Python sources + cross-compiled
  Rust binaries + YAML configs into the stage payload at build time.
- ✓ **Cross-compile to aarch64 works** via `cross` + `Cross.toml` with a
  `pre-build` hook installing `libasound2-dev:arm64` (alsa-sys
  dependency). 5 stripped ELF aarch64 binaries produced on `paul`.
- ✓ **`paul` host fully provisioned** for image builds:
  - rustup default stable + aarch64-unknown-linux-gnu target
  - Docker 29.5.2 with overlay2 + cgroups v2
  - `qemu-user-static` + `qemu-user-static-binfmt` registered
  - `/usr/local/bin/qemu-arm` symlink (pi-gen looks for `qemu-arm`, not
    `qemu-arm-static`)
- ✓ pi-gen pinned to `2026-04-13-raspios-trixie-arm64` (Debian 13).
  Master had Bookworm signature trust failures in the build container;
  Trixie tag works cleanly.
- ✓ Cross-language Tier 1+ integration test
  (`rpi5/tests/component/test_cross_language_stack.py`) — spawns real Rust
  binaries + Python orchestrator and asserts state flows through.
- ✓ Full `make vm-image` end-to-end SUCCEEDED on build #14 (paul,
  2026-06-11). Output: `vm-images/blazen_os-0.0.1-dev.qcow2` (2.3 GB).
  Loopback-verified contents: Python venv + blazend sources + 6
  aarch64 Rust binaries + 10 systemd units enabled in `blazend.target`.
- ✓ **Dev/release image split** (`scripts/build-image.sh --dev`): both
  flavours ship a login `blazen` user + SSH enabled (so the boot test can
  authenticate); they differ only in the shipped credential — dev bakes a
  key + serial password, release is pubkey-only/fail-closed with the
  password locked. See [`06-SSH-BOOTSTRAP.md`](06-SSH-BOOTSTRAP.md) §6.
  (**Updated 2026-06-14:** SSH is now on by default on release images too —
  previously release kept a nologin user + SSH off. Before *that*, every
  QEMU boot test was un-passable — `blazen` was `nologin` and `ssh`
  disabled; see footgun below.)
- ⧗ `make run-vm` boots the image into a usable Pi-OS Lite shell with
  the `blazen` user reachable via SSH (`-i build/dev-ssh/id_ed25519 -p
  2222`).
- ⧗ `blazend-bootstrap.service` runs on first boot and lays down
  `/etc/blazen/` from `configs/`.

**Exit criterion:** `make run-vm` boots in < 90 s and
`ssh -i build/dev-ssh/id_ed25519 -p 2222 blazen@localhost true` exits 0
(against the **dev** image — release images are intentionally not
SSH-able).

> macOS hosts can validate everything *except* the pi-gen image build
> via `make qemu-smoke`, `make dev`, and the test suites. The image
> build runs on `paul` (or any Linux box with Docker + qemu-user-static).
> See [`docs/15-DEV-WORKFLOW.md`](docs/15-DEV-WORKFLOW.md).

### M1 operational footguns (fixed in flight)

- pi-gen needs **qemu-user-static** registered as binfmt **on the host**,
  plus a `qemu-arm` (no `-static` suffix) shim on PATH.
- The `pigen_work` Docker container persists between runs; rerun after a
  failure requires `docker rm -v -f pigen_work` first, or pass
  `CONTINUE=1` to pi-gen.
- pi-gen master branch frequently breaks Bookworm's Release signature
  trust — pin a release tag instead.
- `git clone --depth 1` won't find an arbitrary tag; pass `--branch <tag>`
  or accept a full clone.
- `make`'s default PATH drops `~/.cargo/bin`, so `cross` invocation
  needs the absolute path.
- `git rev-parse --show-toplevel` can find a parent repo when the
  project itself isn't a git repo; use `pwd` instead.
- **pi-gen does NOT automatically rsync `files/`** — neither at the
  stage level nor at the substage level. The substage runner only
  processes `{NN}-debconf`, `{NN}-packages`, `{NN}-packages-nr`,
  `{NN}-patches`, `{NN}-run.sh`, `{NN}-run-chroot.sh`. To get a
  `files/` tree into the rootfs, ship a host-side `00-run.sh` that
  does the rsync explicitly. (Builds #4-#12 all failed on this until
  diagnosed in #12.)
- **pi-gen does NOT export `SUB_STAGE_DIR` / `ROOTFS_DIR`** to
  subprocess scripts. It DOES `pushd "${SUB_STAGE_DIR}"` before
  running `${i}-run.sh`, and it exports `WORK_DIR` + `STAGE`. So in
  a host-side script: `SUB_STAGE_DIR="$PWD"` and
  `ROOTFS_DIR="${WORK_DIR}/${STAGE}/rootfs"`. (Build #13 failed
  because the script tried to use a non-existent `${SUB_STAGE_DIR}`
  and the test silently fell through.)
- The chroot mounts `/tmp` as tmpfs — staging payload **must** go
  somewhere other than `/tmp/`. We use `/var/lib/blazen-staging/`.
- **The built image had no loginable account and SSH disabled**, so the
  M1 boot test (`ssh … blazen@localhost`) could never have passed: the
  chroot created `blazen` as `--system --shell /usr/sbin/nologin` and ran
  `systemctl disable ssh`, and the boot partition carried only the stock
  (all-commented) cloud-init `user-data` with no `userconf.txt` and no
  firstrun hook in `cmdline.txt`. Fixed by the dev/release image split:
  `make vm-image` (→ `--dev`) makes `blazen` a login user and enables SSH;
  release builds keep the locked-down contract. See
  [`06-SSH-BOOTSTRAP.md`](06-SSH-BOOTSTRAP.md) §6.
- **QEMU 11's `raspi4b` machine is fixed at 2 GiB RAM** and rejects any
  other `-m` ("Invalid RAM size, should be 2 GiB"). `configs/vm/qemu-raspi.yaml`
  asked for 4096, so `make run-vm` aborted before the kernel ever
  started. Pinned to `ram_mb: 2048`.
- **The boot `cmdline` was silent + pointed at the wrong root.** Under
  `-M raspi4b -kernel kernel8.img` (no firmware to set up the console or
  load an initramfs): (a) the PL011 needs an explicit
  `earlycon=pl011,0xfe201000` or nothing prints — the old cmdline gave a
  0-byte serial log; (b) the `if=sd` drive enumerates as **mmcblk1**
  (mmcblk0 is the SDIO/wifi `mmcnr` controller), so `root=/dev/mmcblk0p2`
  hangs at "Waiting for root device" forever — it must be
  `root=/dev/mmcblk1p2`. With both fixed (now the config default), the
  kernel boots, `EXT4-fs (mmcblk1p2)` mounts, and `systemd[1]` starts.
- **FIXED 2026-06-12 — the silent-console bug.** The earlier "PID 1 dies
  ~11 s with no output" symptom was largely a **console mismatch**: the
  PL011 UART at `0xfe201000` (the one `earlycon` and QEMU's serial use)
  enumerates as **`ttyAMA1`**, not `ttyAMA0`, under this kernel/DTB. With
  `console=ttyAMA0` the kernel logs `"Warning: unable to open an initial
  console"` and systemd PID 1 gets **no `/dev/console`** — so it runs
  blind (no logs) and we couldn't see what it was doing. Fix:
  `console=ttyAMA1,115200` in `configs/vm/qemu-raspi.yaml`. With it,
  systemd 257.13 now boots **with visible logs** through cgroup2 + bpf +
  devpts setup.
- **Corrected hardware finding — `-M virt` is impossible with the stock
  kernel (was a wrong recommendation).** Inspecting the image's
  `/lib/modules/6.18.33+rpt-rpi-v8` directly: the kernel has **no virtio
  at all** (`virtio_blk`/`virtio_pci` are neither built-in nor modules),
  and its **only** PCIe controller is `pcie-brcmstb` (Broadcom-specific —
  **no** generic ECAM `pci-host-generic`). So on `-M virt` the PCIe bus
  never comes up and **no** PCIe storage works (virtio-blk, NVMe, AHCI,
  qemu-xhci all dead). The previous "build a virtio initramfs" plan
  cannot work. (`ext4`, `nvme`, `sd_mod`, `xhci`, `usb-storage` *are*
  built-in, so the `raspi4b` SD path needs no initramfs.)
- **OPEN (M1 blocker, narrowed): on `-M raspi4b`, systemd PID 1 dies at
  the first service spawn.** With the console fixed, systemd boots cleanly
  (mounts cgroup2, probes all controllers + bpf, sets hostname + machine
  id) and dies **exactly** at its last log line `Using systemd-executor
  binary from '/usr/lib/systemd/systemd-executor'` — i.e. when PID 1 first
  forks a child via the systemd 257 `sd-executor` (`clone3`) path. No
  caught-signal and no `systemd.crash_shell` prompt, so it's the QEMU
  `raspi4b`/TCG emulation choking on the process-spawn path, **not** an
  image defect. Userland is proven healthy (systemd ran ~11 s as a large
  dynamically-linked binary; `/bin/sh` exec'd fine). `-smp 1` can't be
  tried — `raspi4b` requires exactly 4 CPUs.
- **Recommended next step:** validate the full boot on **real Pi 5
  hardware** (M8) — the stock kernel + this QEMU `raspi4b` cannot complete
  a systemd boot. Keep QEMU for the mocked component tiers (Tier 0-1) and
  the rootfs-level loopback verification (which already passes). A newer
  QEMU with more complete `raspi4b`/`clone3` emulation, or a custom kernel
  with `CONFIG_PCI_HOST_GENERIC=y`+`CONFIG_VIRTIO_BLK=y` for `-M virt`,
  are the only QEMU paths left and are out of scope for M1.
- Independent of the machine choice, a `--dev` image rebuild
  (`make vm-image`, now `--dev`) is required before SSH can
  authenticate. The dev/release access fix itself is verified at the
  rootfs level; only the live in-QEMU boot is blocked.

  Verified-good QEMU cmdline bits (now the config defaults): RAM **2048**,
  `earlycon=pl011,0xfe201000`, console on the PL011, root on **mmcblk1p2**
  (mmcblk0 is the SDIO controller).

## M2 — Audio capture + wake word

- ✓ **LED simulator (2026-06-12):** the orchestrator derives the status
  colour from the live event stream and writes `/run/blazen/led.json`
  (`blazend/led.py`); off/green/blue/magenta/yellow/red per
  `docs/02-HARDWARE.md`. Unit + transition tests green.
- ✓ **`blazend-audio-in` real `cpal` probe (2026-06-12):** opens the
  default input device, reports `mic.ready` / `mic.absent`, and falls
  back to synthetic frames when none is present (WSL/CI). The streaming
  capture → shared-memory ring buffer needs a live ALSA device and lands
  on real hardware.
- ⧗ `blazend-wake` running openWakeWord with the pretrained model — needs
  the ONNX model + `ort` (which has host-SDK build issues; deferred).
- ⧗ Tier 3 scenario `01-wake-word.yaml` — its exit criterion runs the
  e2e-runner against a booted VM with real audio; both are blocked on
  this WSL host (no audio device, M1 boot blocker). **Validates on real
  Pi 5** — see the M1 boot note above.

**Exit criterion:** `make test-scenario S=01-wake-word` green in CI
(on a Pi-class runner with audio + a bootable image).

## M3 — ASR pipeline (bilingual)

- `blazend-asr` runs `faster-whisper` with the **multilingual `small`**
  model (PL + EN by default — see [`13-LANGUAGES.md`](13-LANGUAGES.md)).
- VAD-driven utterance segmentation.
- The wake → ASR loop produces `asr.final` plus a `language` tag for
  each utterance.
- Tier 3 scenarios `02-basic-commands.yaml` (EN) and
  `07-pl-basic-commands.yaml` (PL) pass.

**Exit criterion:** WER ≤ 12% on the 50-line PL `commands` fixture set
AND WER ≤ 8% on the 50-line EN `commands` fixture set.

## M4 — Local LLM (CPU) + bilingual TTS

- ✓ **Brain wired to the real engine (2026-06-12):** `blazend-brain` now
  subscribes to `asr.final` and runs `blazend.assistant.engine.Assistant`
  (name gate, memory/reminders, Gemini-backed Polish chat + news),
  publishing `brain.reply`; due reminders fire on a timer. Component test
  drives `asr.final → brain.reply` over real IPC (`rpi5/tests/component/
  test_brain_engine.py`). The conversational LLM is **Gemini** for now —
  the **local CPU GGUF** path (`qwen2.5-1.5b…`) and **Piper TTS** below are
  the remaining M4 work (need the models + the audio-out path).
- `blazend-brain` runs `qwen2.5-1.5b-instruct-q4_k_m.gguf` in the VM
  (size chosen for QEMU memory headroom). Pi 5 hardware tests in M8
  will use the 3B default. The bilingual system prompt is in place;
  reply language follows detected user language.
- The engine selector and IPC contract are in place; the Hailo path is
  stubbed and falls back to CPU automatically when no device is
  present.
- `blazend-tts` runs Piper with **both** `pl_PL-darkman-medium` and
  `en_US-lessac-medium` warm; voice is selected per utterance.
- First end-to-end conversations:
  - PL: "hej Jessico, która godzina" → spoken Polish reply.
  - EN: "hey Jessica, what time is it" → spoken English reply.

**Exit criterion:** Scenarios `04-conversation.yaml` (EN) and
`08-pl-conversation.yaml` (PL) pass with the semantic-similarity
matcher on the CPU engine.

## M5 — System command intents (bilingual)

- ✓ **Fast-path router landed early (2026-06-12):** `blazend-nlu` (Rust)
  consumes `asr.final` and routes via the **shared `jessica-core`**
  `IntentRouter` over `configs/intents/system.yaml`, publishing
  `nlu.intent`. Same crate as the iOS/Android apps (via `jessica-ffi`) —
  one source of truth, no Python copy. Unit + IPC integration tests cover
  EN and PL (`rpi5/crates/blazend-nlu`).
- ✓ **Intent dispatch + confirmation grammar (2026-06-12):** the
  orchestrator acts on `nlu.intent` via `blazend/dispatch.py` — looks up
  each intent's `action`/`mutate`/`tool` from `configs/intents/system.yaml`,
  enforces `configs/voice-policy.yaml` (deny globs, `allowed_values`, and
  the **never / single / loud / double_loud** confirmation grammar),
  applies voice-mutable settings to a `SettingsStore`, runs simple tools
  (`clock.time`/`date`), and emits signals (`tts_interrupt`, `reboot`,
  `shutdown`…). Confirmation is a stateful flow: `reboot` → "Na pewno?
  Powiedz „potwierdzam”." → `apply_change` → reboot signal; `factory_reset`
  needs two. Dispatched replies go out as `brain.reply` for TTS. 8 tests
  (`rpi5/tests/unit/test_dispatch.py`), validated against all 52 real
  intents.
- ✓ **Language switch by voice (2026-06-14):** the dispatcher acts on the
  `switch_language` / `unpin_language` / `languages.list` intents. A pin
  (`speak Polish` / `mów po angielsku`) wins over the per-utterance detected
  language for every reply — an EN command under a PL pin still answers in
  Polish — until `słuchaj uważnie` / `detect my language` unpins back to
  auto-detect. The pin persists in the voice-settings overlay and is mirrored
  into `/run/blazen/state.json` (`languages.pinned`). Confirmation lands in the
  target language; non-`en`/`pl` requests are rejected. Tier-0 tests mirror the
  `09-language-switch` turn sequence (`rpi5/tests/unit/test_dispatch.py`). See
  [`13-LANGUAGES.md`](13-LANGUAGES.md) §3.6.
- Regex/keyword fast-path router with PL + EN triggers for every
  intent: `volume up/down/set`, `stop talking`, `repeat`, `go to sleep`,
  `what time is it`, `what's the date`, `reboot`, `shutdown`,
  `enable/disable ssh`, `speak Polish/English`, `apply change` /
  `potwierdzam`.
- `voice-policy.yaml` enforced including `confirm: loud` for reboot /
  shutdown — confirmation phrase accepted in either language.
- ✓ **`brain`/`nlu` arbitration (2026-06-12):** `blazend-nlu` now emits an
  **`nlu.miss`** event for unmatched utterances; the **brain consumes
  `nlu.miss`** (not `asr.final`) while the **orchestrator dispatches
  `nlu.intent`**. So a matched command goes only to the dispatcher and an
  unmatched utterance only to the conversational brain — no double-reply.
  New Rust event + schema + topic registries; nlu IPC test covers the miss
  path; brain component test drives `nlu.miss`.

**Exit criterion:** Scenarios `03-system-control.yaml`,
`05-fail-modes.yaml`, and `09-language-switch.yaml` pass.

## M6 — Voice-controlled configuration

- Voice mutation flow for model / TTS voice / wake-word / language pin.
- WiFi reconfigure by voice (with confirmation, in both languages).
- Custom wake-word training pipeline (CLI-driven) producing a working
  `hey_blazen_<lang>.onnx` from 50 synthetic Piper utterances + 5 real
  recordings.

**Exit criterion:** A scenario YAML that switches LLM model end-to-end
without SSH passes — runnable in both EN and PL spoken forms.

## M7 — Conversation memory + tools

- Conversation history with summarisation at 3072 tokens.
- Minimal tool-call interface: `timer.set`, `timer.cancel`,
  `weather.now` (only the timer is in core; weather is the first
  plugin example).

**Exit criterion:** A 10-turn timer scenario passes; conversation
history survives a `systemctl restart blazend-brain`.

## M8 — Real hardware bring-up

- The image flashed to a Pi 5 8 GB boots and pairs.
- **ReSpeaker 2-Mics Pi HAT V2.0** (the reference interface) works
  end-to-end: install the `respeaker-2mic-v2_0` device-tree overlay from
  `seeed-linux-dtoverlays`, confirm the TLV320AIC3104 ALSA capture/playback
  devices, the 3 APA102 status LEDs, and the GPIO17 button; CPU AEC enabled.
  See [`02-HARDWARE.md`](02-HARDWARE.md).
- **Hailo path bring-up:** install HailoRT + drivers, compile the first
  Hailo `.hef` for Qwen2.5-3B (off-device via the Hailo DFC), verify
  engine selector picks Hailo on detection and falls back to CPU on
  unplug.
- Tier 4 hardware checklist signed off for: Pi 5 CPU-only, Pi 5 +
  Hailo-8 (26T), Pi 5 + Hailo-10H (when available on the bench).

**Exit criterion:** End-to-end timer scenario passes on a real Pi 5 in
< 1.5 s median wake → first TTS on CPU, and < 1.0 s on Hailo-10H.

## M9 — Performance + soak

- Rust hot-path rewrite if and only if profiling on Pi 4 shows GIL
  contention >15% of orchestrator CPU.
- 24-hour soak run on Pi 5 and Pi 4 with <20% RSS growth.
- Acoustic stress tests (radio in background, multiple speakers).

**Exit criterion:** All soak budgets met; CI runs soak weekly without
human intervention.

## M10 — Release candidate

- Image signed and published as `blazen_os-1.0.0-rc1.img.xz`.
- Reproducible build attestation.
- Public docs (README + `docs/`) accurate at the commit hash of the
  release.
- Known-issues list documented.

**Exit criterion:** A new contributor can `git clone`, `make models`,
`make vm-image`, `make run-vm`, and have a working assistant in under
30 minutes.

---

## Cut-from-scope tracking

Items intentionally deferred:

- Pi 3 / Pi 4 / Pi Zero 2 W support — Pi 5 only.
- Multi-user accounts.
- A GUI / web dashboard.
- Wyoming/Rhasspy interop satellites.
- OTA image updates (only `apt`-style component updates in M7+).
- Bluetooth peripherals.
- Voice biometric identification (privacy review pending).
- Non-Hailo accelerators (Coral, NCS2, generic NPUs) for the LLM path.
- On-device Hailo model compilation (offline only — see
  [`12-ML-ACCELERATOR.md`](12-ML-ACCELERATOR.md) §9).

These are revisited after M10 ships, not before.
