# HANDOFF — `blazen_os` ↔ `rachel` workflow

Created **2026-06-11** during the macOS → paul session transition.

---

## Session log — 2026-06-15 (jessica — REAL Pi 5) — first on-hardware bring-up

First time the repo has been checked out and tested on **actual Pi 5
hardware** (not paul/WSL2 TCG-QEMU, not the Mac). New host: **`jessica`**
— Raspberry Pi 5 Model B Rev 1.0, **8 GB** (supported-secondary, not the
16 GB reference), Debian 13 trixie, kernel `6.12.75+rpt-rpi-2712`,
Python 3.13.5, cargo/rustc 1.96.0. The checkout arrived via rsync (carrying
paul's build artefacts), **not** `git clone`, which is what surfaced the
issues below. Git tree clean at `235fb0a`.

**Result: full Tier 0+1 pyramid GREEN natively on aarch64.**
`make test-fast` exit 0 — **97 Python + 27 Rust core + 11 Rust appliance =
135 tests**. `make audit` exit 0 (model SHAs still `<TBD>`, expected — no
weights downloaded). Counts grew since the 2026-06-11 baseline (64 py / 27
rust) because of the M5 work now on `main` (fail-modes/health, NLU lang-switch).

**What had to be done to make it run here (host provisioning, not repo bugs):**

1. **Stale x86-64 `.venv`** — the rsync'd `.venv/bin/python3` was an
   x86-64 ELF (built on paul, Python 3.14.5); it `Exec format error`'d on
   aarch64. `make venv` will **not** self-heal this — the target file
   exists so make skips it. Fix: `rm -rf .venv && python3 -m venv .venv`
   then `pip install -e ".[dev]"`. pydantic-core/numpy resolved as
   prebuilt aarch64 wheels (no source compile). `.venv` is gitignored so
   this never touches git.
2. **Stale x86-64 Cargo `target/`** — both `crates/target` and
   `rpi5/crates/target` held paul's x86-64 `.rlib`s. Removed both; cargo
   rebuilt clean for the aarch64 host.
3. **`libasound2-dev` missing** — `cpal` (blazend-audio-in/out) needs it.
   It **is** already in `scripts/install-deps.sh`, so the real gap is that
   this host was rsync-provisioned and `make install-deps` was never run
   here. A concurrent first-boot `apt-get` (build-essential/clang/postgis)
   had also left dpkg interrupted — `sudo dpkg --configure -a` then
   `apt-get install -y libasound2-dev`.

   → **Next time you put the repo on a fresh Pi, `git clone` it (don't
   rsync paul's tree) and run `make install-deps` first.** That avoids all
   three of the above.

**Note on the two cross-language component tests** — they only `pytest.skip`
when the Rust binaries are absent (the guard paths
`rpi5/crates/target/{debug,release}` are correct for the post-split layout).
After `cd rpi5/crates && cargo build --workspace`, both run and pass — the
final 97/97 includes them. No code change needed.

**Live stack validated on hardware.** Ran `scripts/dev-run.sh` (all 8 units,
mock mode — no HAT needed) on the Pi: full Polish round-trip end-to-end —
wake (`hey_blazen_pl`, score 0.75) → ASR `'która godzina'` → NLU matched
`what_time/pl` (52 intents loaded) → command dispatched, `state.json`
`ready: true`, LED state machine driving `magenta/processing`, orchestrator
connected to all 8 peers, **zero errors/panics** across every unit log. The
cross-language (Rust + Python) IPC pipeline works natively on aarch64.

**Hygiene pass + new `make lint` gate (user-requested "full pass + enforce").**
The lint/format/type checks were not run in any gate, so debt had accumulated
on `main` (surfaced once the fresh aarch64 venv pulled newer ruff 0.15 /
mypy 2.1 and a floating-`stable` rustfmt 1.9). Cleaned it all and wired a gate
so it can't recur:
- `cargo fmt` both workspaces (14 `.rs` files); both now `fmt --check`-clean.
- `ruff --fix` across `rpi5` (src + tests) + `scripts` — clean.
- **21 mypy-strict errors fixed surgically** (bare `dict`→`dict[str, Any]`,
  `Returning Any` via typed locals, missing param annotations, an `int(None)`
  guard in `dispatch._resolve_value`, a `None` guard in
  `supervisor._dispatch_intent`). Added `types-PyYAML` to the `dev` extra.
- `clippy -D warnings` both workspaces — removed a dead `CoreError` import in
  `jessica-core/src/intent.rs`.
- **New `make lint`** (ruff + mypy + rustfmt `--check` + clippy, both
  workspaces) is now a **prerequisite of `make test-fast`**, so format/type
  drift fails the gate before tests run. Documented in `AGENTS.md` §3 and
  `CLAUDE.md` §4 + §8. `make test-fast` green end-to-end (lint + 97 Python +
  27 core + 11 appliance Rust).

**ReSpeaker 2-Mics Pi HAT enabled + Polish voice recognition proven
(2026-06-15, later).** The HAT (WM8960 codec — the reference interface, commit
`333ae22`) was physically connected but **not enabled in software** (GPIO
`i2c-1` off, no overlay), which is why an earlier check saw only HDMI + two
incidental USB audio devices (Sony INZONE H3, Logitech webcam). Brought it up
and persisted it:
- **Confirmed alive:** enabled `i2c_arm` at runtime → WM8960 answers at I2C
  **`0x1a`** (shows `UU` once driver-bound).
- **Enabled the card:** `dtoverlay wm8960-soundcard` (mainline overlay is
  present; the seeed-voicecard DKMS driver is *not* needed on kernel 6.12) →
  registers as ALSA **card `wm8960soundcard`** with capture **and** playback.
  Loaded at runtime (no reboot, session survived).
- **Persisted** in `/boot/firmware/config.txt` (backup at
  `config.txt.bak-blazen`): uncommented `dtparam=i2c_arm=on` + `=i2s=on`,
  appended `dtoverlay=wm8960-soundcard`. Mixer routing for the two mics
  (`Input Mixer Boost` on, `Input Boost Mixer LINPUT1/RINPUT1`=2, output
  bypass off to avoid feedback, Capture PGA ~60%) saved via `alsactl store`
  (restored on boot by `alsa-restore.service`). Quiet-room capture now sits at
  RMS ~750 / peak ~7–9k — clean headroom for speech.
- **No speaker attached** to the HAT (2-Mics HAT has none onboard) — an
  acoustic speaker→mic loopback reads ambient only, so a fully autonomous
  end-to-end voice test isn't possible without an external speaker.

**Voice recognition + Polish — works on the Pi 5 CPU.** Installed
`faster-whisper 1.2.1` (+ ctranslate2 4.8.0, aarch64 wheels) and the `small`
multilingual model (`Systran/faster-whisper-small`, the documented Polish
default in `configs/asr.yaml`). Proof: a synthesized Polish phrase (espeak-ng
`pl`) → Whisper → `lang=pl p=1.00`, correct Polish text + diacritics (last word
mangled only because espeak's robotic TTS is hard to recognise — a human voice
is far cleaner). New diagnostic **`scripts/hat-voice-check.py`** records from the
HAT mic and transcribes (`--lang pl --model small`, `--loop`); run it and speak
to see live Polish recognition. On-device throughout; nothing leaves the Pi.
Latency note: `small` int8 on CPU is ~4× real-time — fine for a check, an M2
perf concern for the live path.

**Real ASR pipeline BUILT + wired (M2), Polish-first.** The former empty stub
(`if not mock: await asyncio.Event().wait()`) is replaced by the full
architecturally-pure path (plan in `~/.claude/plans/quizzical-exploring-cosmos.md`,
user-approved). New code:
- **Rust `blazend-audio-in`** (`ring.rs`, `vad.rs`, rewritten `main.rs`):
  real `cpal` capture → downmix→16 kHz → **shared-memory ring**
  (`runtime_dir()/audio-ring.shm`, self-describing `BZAR` header) + an energy
  **VAD** publishing `vad.start`/`vad.end`, plus a ~1 s heartbeat (so
  `blazend-health` doesn't flag mic starvation). Picks a real input device by
  name (`--device wm8960`) — fixes the "ALSA default has no capture slave" trap
  — and selects a codec-clockable rate (16 kHz; cpal's guessed 44100 yielded a
  silent stream on the WM8960). Keeps `--mock`.
- **Python `blazend.audio`** (`RingReader`/`RingWriter`) mirrors the ring byte
  layout; **`blazend.asr.engine.Transcriber`** wraps faster-whisper (lazy
  import) with **Polish-first** coercion (detect → `{pl,en}`, tie-break `pl`) +
  confidence from `avg_logprob`; **`blazend.asr.__main__`** real path subscribes
  to the VAD markers, reads the utterance slice from the ring, transcribes
  off-thread, publishes `asr.final` → `blazend-nlu`.
- Config: `configs/audio.yaml` gained an `input.vad:` block (and the codec
  comment was corrected WM8960/`wm8960-soundcard`, not TLV320AIC3104).
  `scripts/dev-run.sh` runs the real path under `BLAZEN_REAL_AUDIO=1`
  (`--device wm8960`, `BLAZEN_ASR_MODEL=small` for the 8 GB Pi).
- Tests: Rust ring/VAD unit tests (5); Python ring roundtrip + engine PL/EN
  parity with a fake backend (9). `make test-fast` green (**106 Python + 27
  core + 16 appliance Rust**); `make lint` clean (ruff/mypy/fmt/clippy).

**Verified on this Pi 5 + HAT:** real capture fills the ring (18.5 s continuous,
`write_pos` advancing); Python reads it; **ring→reader→engine on a synthesized
Polish phrase** → `lang=pl`, correct Polish text; full `BLAZEN_REAL_AUDIO=1`
stack runs clean (audio-in on `wm8960soundcard`, asr `small` subscribed, **0
panics/POLLERR**). The one thing left is **live calibration**: the cpal capture
RMS is gain-sensitive, so `open_rms`/`close_rms` (in `audio.yaml` / `--open-rms`)
need tuning to the operator's voice+room — I can't speak to set them. Use
`scripts/hat-voice-check.py` (bypasses VAD) for the live "speak Polish" check.

**Not done (out of scope):** wake-gating (always-listening for now; no
openWakeWord models on disk), `asr.partial` streaming, an in-VM e2e ASR
scenario (needs audio injection), image build, TTS models. ASR latency
(`small` int8 ~4× real-time on CPU) is an M2 perf follow-up.

---

## Session log — 2026-06-11 (paul) — monorepo consolidated on origin

The full monorepo now lives on `origin/main` and `make test-fast` is green
(64 Python + 27 Rust; both Cargo workspaces). Final layout:

```
blazen_os/
  crates/   shared Rust core (blazend-ipc, blazend-fabric, jessica-core, jessica-ffi)
  configs/ docs/ scripts/   shared contract + docs + tooling
  android/  ios/   native mobile (in-repo; were sibling repos)
  rpi5/     Raspberry Pi 5 appliance (Python + appliance crates + pi-gen + tests)
```

Commits: `ff9eafd` (rpi5/ split + jessica-core rename) → `4bb19a9`
(android/ + ios/ consolidated, docs/17 map). **Decision: the rpi5/ split
wins** over the earlier "Pi 5 stays at root" wording — `docs/17` was
reconciled to match. No android/ios *code* broke (they consume
`crates/jessica-ffi` symbols + `configs/intents/system.yaml`, both stable
at root); only doc/comment refs were renamed to `jessica-core` / `rpi5/…`.

**⚠️ rachel session — converge your local copy.** Your `~/dev/blazen_os`
still has the pre-consolidation **uncommitted** android/ios/docs17 +
local edits to AGENTS/CLAUDE/README/00-INDEX. origin now carries the
merged versions, so a plain `git pull` will refuse (untracked + local
changes). Since origin already contains this work, the lossless recipe is:

```
cd ~/dev/blazen_os
git fetch origin
git stash -u                 # park your local copy (recoverable)
git reset --hard origin/main # adopt the consolidated monorepo
git stash drop               # once you've confirmed nothing unique was parked
```

If you made edits AFTER the consolidation snapshot, diff `git stash show -p`
before dropping. Then rebuild: `make test-fast` (Pi 5), and your mobile
builds (`cd android && make build`, iOS on the Mac).

---

## Session log — 2026-06-11 (paul) — monorepo restructure: rpi5/ project + shared core

paul Claude split the repo into a **shared core** (top level, common to
iOS, Android, Rpi5) and a self-contained **Raspberry Pi 5 appliance
project** under `rpi5/`, and renamed the shared crate. `make test-fast`
green (63 passed + 1 env-skip Python, 27 Rust; both Cargo workspaces).

**New layout** (full tree: `docs/14-RUST-PYTHON-SPLIT.md` §4):

- **Top level = shared core** (consumed by all 3 platforms):
  - `crates/` — Cargo workspace: `blazend-ipc`, `blazend-fabric`,
    **`jessica-core`** (⚠️ renamed from `jessica-mobile-core`), `jessica-ffi`.
  - `configs/`, `docs/`, `scripts/` — unchanged locations.
- **`rpi5/` = appliance project** (device-only): `rpi5/src/blazend`
  (Python), `rpi5/crates/*` (appliance Cargo workspace: audio-in/out,
  wake, tts, health — path-depend on `../../crates`), `rpi5/stage-blazen`,
  `rpi5/tests`, `rpi5/pyproject.toml`, `rpi5/Makefile` (forwards to root).

**⚠️ Mobile (ios/android/rachel) impact — rachel Claude read this:**

- **Nothing mobile-facing moved.** `crates/jessica-ffi` and `configs/`
  keep their paths, so the `ios`/`android` build scripts
  (`scripts/build-{ios,android}*.sh`) and shared-contract asset paths
  (`configs/intents/system.yaml`, `configs/_schema/events`) are **stable**.
  Mobile builds should be unaffected.
- **One rename to mirror:** the Rust crate `jessica-mobile-core` →
  `jessica-core`. The C-ABI/JNI symbols (`jessica_ffi_*`,
  `JessicaCoreNative`) are **unchanged** — only the internal crate name.
  If any ios/android/rachel doc or comment names `jessica-mobile-core`,
  update it to `jessica-core`. No code change needed on the mobile side.
- **Rust core is now explicitly common to all 3.** `jessica-core` +
  `blazend-fabric` are declared available to the appliance workspace
  (`rpi5/crates/Cargo.toml`). There is **no Python intent router** — the
  appliance had none; routing stays single-source in `jessica-core`. The
  appliance's future NLU unit will consume it directly (no duplicate).
- **Action for rachel Claude:** `make sync-pull`, then re-run your
  ios/android builds to confirm green (expected: yes), and grep your
  repos for `jessica-mobile-core` → rename to `jessica-core` in docs.

**Still open (unchanged):** M1 QEMU boot blocker (see earlier paul log).

---

## Session log — 2026-06-11 (paul) — git sync + hardware-version doc parity

paul Claude moved the cross-host sync from **rsync → git/GitHub** and
cleaned up post-rebrand drift on the Pi 5 appliance surface.

**⚠️ Sync transport changed — rachel/macOS Claude, read this:**

- **paul's `~/dev/blazen_os` is now a real git repo** wired to origin
  `git@github.com:Marysia-Software-Limited/BlazenOS.git` (branch `main`,
  181 files == rachel, `make test-fast` green). Previously paul only had
  an rsync mirror with no `.git`.
- **rachel's local `4730d4d` ("Rebrand … Jessica") was unpushed; I
  published it to origin** (clean fast-forward c5dbcc7→4730d4d). origin
  is now current. Nothing of rachel's was changed — just pushed.
- **New workflow (both sides):** `make sync-pull` → commit →
  `make sync-push` (gates on `test-fast`, refuses dirty/red). From paul,
  `make rachel-pull` fast-forwards rachel. Old `make push-paul/pull-paul`
  rsync targets are **deprecated** (bulk artefacts only). Full rules
  rewritten in `docs/16-SYNC-PROTOCOL.md` + `docs/15-DEV-WORKFLOW.md` §3.
- **Action for rachel Claude:** `git pull --ff-only origin main` to pick
  up this session's commit before your next edit.

**Hardware-version (Pi 5 appliance) doc parity fixes:**

- `docs/05-MODELS.md` system-prompt block still showed the pre-rebrand
  *"You are blazen…"* — replaced with the canonical Jessica prompt from
  `configs/llm.yaml` (now points to that file as source of truth).
- Reference-platform RAM contradiction resolved: the dated decision in
  `docs/02-HARDWARE.md` is **Pi 5 16 GB reference / 8 GB supported
  secondary**, but `CLAUDE.md`, `AGENTS.md`, `README.md` still said
  "8 GB" only. Aligned all three to 16 GB-reference.
- Verified the rest of the appliance voice surface (wake-word.yaml,
  scenarios 01–09, persona doc) is already consistently Jessica with full
  EN+PL parity — no drift there.

**Still open (unchanged):** the M1 QEMU boot blocker
(`Attempted to kill init! exitcode=0x00000100` ~11 s on `-M raspi4b`).
Recommended next HW task remains: tiny initramfs with `virtio_pci`+
`virtio_blk` for `-M virt`, or validate boot on a real Pi 5. See the
earlier paul session log below.

---

## Session log — 2026-06-11 (macOS) — Native mobile scaffolds landed

macOS Claude rebooted the mobile stack per user's explicit ask
(`zrob osobne projekty /android i /ios i stworz natywne aplikacje`).
Net: real, building, testable native projects sitting at
`/Users/beret/dev/ios/` and `/Users/beret/dev/android/`.

- **`/Users/beret/dev/ios/`** — Swift Package + xcodegen-driven
  app. 5 SwiftUI screens (3 onboarding + Home/Settings tabs + Pairing
  with QR-scan & manual code fallback). `JessicaCore` Swift Package
  (3 unit tests — **green**, verified via Xcode-beta swift 6.4
  toolchain because macOS 27 CommandLineTools ship a broken
  swift-package). Voice surface uses `actor`s wrapping
  `SFSpeechRecognizer.requiresOnDeviceRecognition = true` (audio never
  leaves the device). Fabric client scaffold uses `NWBrowser` for
  `_jessica._tcp`. Keychain-backed `SecureStorage`. Info.plist with
  Polish usage strings, Bonjour service entry, background modes
  (audio/processing/fetch), BGTaskScheduler IDs.

- **`/Users/beret/dev/android/`** — Gradle Kotlin DSL project,
  AGP 8.7 + Kotlin 2.0.20 + Compose BOM 2024.09. `:core` JVM module
  (pure Kotlin port of JessicaCore, 3 JUnit-5 tests written) + `:app`
  Android module (Compose 5 screens, Material3, NavCompose).
  EncryptedSharedPreferences for session state. AndroidManifest with
  MIC + foreground-service-MIC + calendar/contacts/camera/location
  permissions. Adaptive launcher icon. Strings in `values/` (PL) and
  `values-en/`. `:core` test verification gated on user running
  `brew install gradle && make wrapper` once.

- **docs**: `15-NATIVE-MIGRATION.md` updated with status block;
  per-project README rewritten with concrete file-by-file status
  tables. `09-MOBILE-PLATFORM-DECISION.md` and the path renames
  (jessica-ios → ios, jessica-android → android) from this session
  were already in place.

Pending follow-ups for these projects (intentional — not blockers):
- Task #40 still owned by paul Claude.

**Task #51 (`crates/jessica-ffi`) is now done in the same session:**
- `crates/jessica-ffi/` is a new workspace member shipping a
  `staticlib + cdylib + rlib` crate with 10 C-ABI entry points
  (`jessica_ffi_new/free/free_string/version/load_intents/match_intent/
  intent_count/merge_fact/fact_count/get_fact`) over `Mutex<JessicaInner>`.
  All panics caught at the FFI boundary, all input strings are
  `(*const u8, usize)` non-NUL-terminated buffers, all returned
  strings are caller-owned NUL-terminated `char*`.
- `cbindgen.toml` + `build.rs` + `include/jessica_ffi.h` (101 lines,
  10 functions + 4 numeric error constants + 1 enum with 4 variants).
- `src/jni_bridge.rs` (gated on `target_os = "android"`) wraps the
  same internals for `os.blazen.jessica.core.JessicaCoreNative.*`.
- 3 unit tests in the crate, `cargo test --workspace` green
  (27 tests across blazend-ipc/fabric, jessica-core, jessica-ffi).
- Build scripts: `scripts/build-ios-xcframework.sh` (cargo build
  for aarch64-apple-ios + aarch64-apple-ios-sim + optional x86_64
  simulator slice, lipo, xcodebuild -create-xcframework, rsync into
  `ios/JessicaCore/Frameworks/`); `scripts/build-android-jnilibs.sh`
  (cargo build for aarch64/armv7/x86_64 Android targets via NDK
  clang wrappers, copies into `android/app/src/main/jniLibs/<abi>/`).
- Swift adapter at `ios/JessicaCore/Sources/JessicaCore/JessicaFFI.swift`
  gated on `M1_FFI_AVAILABLE`; Kotlin JNI binding at
  `android/core/src/main/kotlin/os/blazen/jessica/core/JessicaCoreNative.kt`
  gated on `JessicaCoreNative.isAvailable` (runtime
  `System.loadLibrary` check).
- `make ffi` targets in both mobile Makefiles drive the build
  scripts. The FFI itself can be built and tested on a host
  without any of the cross-targets installed — that's a separate
  toolchain install step before flipping the gates.

## Session log — 2026-06-11 (paul) — M1 dev-image access + QEMU boot

paul Claude worked the **hardware (appliance) skin** this session. Net:
the M1 *access* blocker is fixed reproducibly, and the QEMU boot is
diagnosed down to a single remaining open issue. **M1 is not yet
closed** (see "Open" below).

### What changed (paul side, all tested — `make test-fast` green)

- **Dev/release image split** — the real reason the M1 boot test could
  never pass. The chroot created `blazen` as `--system --shell
  /usr/sbin/nologin` and ran `systemctl disable ssh`, and the boot
  partition had only stock all-commented cloud-init `user-data` (no
  `userconf.txt`, no firstrun hook). So the shipped image had **no
  loginable account and no SSH** at all.
  - `scripts/build-image.sh` gained `--dev` / `BLAZEN_DEV_IMAGE=1`:
    sets `ENABLE_SSH=1`, drops a `DEV_IMAGE` marker + a baked SSH key
    into the staging payload.
  - `stage-blazen/00-install/01-run-chroot.sh` branches on the marker:
    dev → login `blazen` (home + bash + passwordless sudo + key +
    `blazen:blazen` serial password) and `systemctl enable ssh`;
    release → (since 2026-06-14) login `blazen` + ssh on too, but
    pubkey-only/fail-closed: password locked, no key baked in.
  - Key comes from `BLAZEN_DEV_SSH_PUBKEY` or is generated at
    `build/dev-ssh/id_ed25519` (gitignored). Marker + key live only in
    `/var/lib/blazen-staging/`, which the chroot deletes — neither ships.
  - `make vm-image` now implies `--dev`; `make pi-image` stays release;
    new `make pi-image-dev` for a flashable dev `.img`.
  - Documented: `docs/06-SSH-BOOTSTRAP.md` §6 (new), `docs/09-VM-TESTING.md`,
    `docs/10-ROADMAP.md` M1.
- **QEMU `raspi4b` config fixes** in `configs/vm/qemu-raspi.yaml`:
  - `ram_mb: 4096 → 2048` (raspi4b is a fixed-2-GiB model; 4096 made
    `make run-vm` abort instantly with "Invalid RAM size").
  - `cmdline` → `earlycon=pl011,0xfe201000 … root=/dev/mmcblk1p2 …`.
    Without earlycon the PL011 console is silent (0-byte serial); the
    `if=sd` drive enumerates as **mmcblk1**, not mmcblk0, so the old
    `root=/dev/mmcblk0p2` hung at "Waiting for root device".

### Boot diagnosis (verified against build #14's qcow2)

With the corrected cmdline the kernel (6.18.33-rpi-v8) boots →
`EXT4-fs (mmcblk1p2)` mounts → `Run /sbin/init` → `systemd[1]` starts →
"Detected first boot" / "Hostname set to <blazen>".

- **Open (M1 blocker): PID 1 dies ~11 s in; no QEMU machine boots this
  userland yet.** Full diagnosis (`-no-reboot` + `systemd.log_target=kmsg`
  + `-initrd initramfs8`):
  - `-M raspi4b`: kernel + `EXT4-fs (mmcblk1p2)` mount + systemd 257.13
    start all OK, then PID 1 dies ~11 s in. *With* `initramfs8` the panic
    is explicit: `Attempted to kill init! exitcode=0x00000100` (init
    exited 1 before switch_root). Not the watchdog (`nowatchdog` no help).
    raspi4b emulation is heavily stubbed (pcie/rng200/thermal/**genet**/
    vc-mem/exp-gpio disabled or failing) — likely the cause.
  - `-M virt`: `initramfs8` runs **cleanly** (no panic) but can't see the
    disk — the Pi initramfs lacks virtio/usb-storage modules, and the rpt
    kernel has `virtio_blk` as a module not built-in, so a no-initramfs
    virt boot hangs at "Waiting for root device".
  - **Recommended (separate task):** build a tiny initramfs with
    `virtio_pci`+`virtio_blk` and boot `-M virt -device virtio-blk-pci
    root=/dev/vda2`, or a kernel with `CONFIG_VIRTIO_BLK=y`; or validate
    boot on **real Pi 5** (M8) and keep QEMU for mocked component tiers.
  - Verified-good cmdline bits now in `configs/vm/qemu-raspi.yaml`: RAM
    2048, `earlycon=pl011,0xfe201000`, root `mmcblk1p2`.
- A `--dev` rebuild (`make vm-image`) is still needed before SSH can
  authenticate; the dev/release access fix is verified at the rootfs
  level, only the live in-QEMU boot is blocked.

### Update (later same session): --dev rebuild DONE + verified

- Full `make vm-image` (now `--dev`) rebuilt clean on paul. Loopback-
  verified the rootfs: `blazen` is `…:/home/blazen:/bin/bash` (uid 1001,
  in `sudo,audio,plugdev`), `~blazen/.ssh/authorized_keys` holds the dev
  key, `/etc/sudoers.d/010-blazen-dev` is present, `ssh.service` is in
  `multi-user.target.wants`, all 10 `blazend-*` units enabled. **The
  dev/release access fix works end-to-end.**
- Fixed a `scripts/build-image.sh` `post_convert` bug found during the
  rebuild: a stale `deploy/*.img` from a prior build shadowed this run's
  fresh `.zip`, so the qcow2 was being converted from the old image. It
  now drops stale raw images and extracts the freshest archive.
- Booting the fresh `--dev` qcow2 reproduces the **same** panic
  (`Attempted to kill init! exitcode=0x00000100` ~11 s) — confirming the
  boot blocker is raspi4b/userland, independent of the dev/release flavour.
- Seen on paul: macOS pushed `docs/product/15-NATIVE-MIGRATION.md` (drop
  Flutter → native `jessica-ios`/`jessica-android` + Rust
  `crates/jessica-core`+`jessica-ffi` in this repo). Propagated to
  rachel; not acted on by paul yet — those crates are future work.

### Cross-repo (for macOS / rachel Claude)

- **No `docs/product/` changes this session** — the shared spec is
  untouched, nothing to pull there.
- The **dev vs release flavour** concept has a mobile analogue (debug
  vs release Flutter build, signing). If you mirror it in
  `docs/platform-mobile/01-BUILD-AND-SHIP.md`, keep the vocabulary
  aligned with `docs/06-SSH-BOOTSTRAP.md` §6 so the two skins read the
  same.

---

## Who owns what

| Repo        | Primary host  | Primary Claude session         | Out of scope here   |
|-------------|----------------|--------------------------------|----------------------|
| `blazen_os` | **paul** (Arch)| The Claude session you open on paul | Mobile app code, Flutter |
| `rachel`    | **macOS** (mac mini / MacBook) | The Claude session on the maintainer's mac | Pi image builds, cross-compile |
| **`docs/product/`** (shared spec) | both | both — coordinate | — |

## What was just done (from the macOS side)

This is the snapshot the macOS session left:

### blazen_os additions / changes (now synced to paul)

- **Jessica fabric** (multi-device federation):
  - `docs/product/{11-FABRIC, 12-PAIRING, 13-RESOURCE-SHARING}.md`
  - User stories US-31..US-38 added to `03-USER-STORIES.md`.
  - New Rust crate `crates/blazend-fabric/` with `Fact`, `PeerInfo`,
    `SyncLog` and a 10-test CRDT merge suite (all green).
  - 5 new JSON schemas under `configs/_schema/events/fabric.*`.
  - 11 new fabric voice intents in `configs/intents/system.yaml`
    (PL+EN: pair, join, leave, kick, share-internet, route-LLM,
    route-audio, route-display, kill-switch).
  - `configs/fabric.yaml` config defaults.
  - New systemd unit `blazend-fabric.service` (now enabled in the
    image).
- **pi-gen layout** — the chroot install moved to
  `stage-blazen/00-install/files/` (substage rsync is what pi-gen
  honours; stage-level files/ is NOT rsync'd). All systemd units +
  the `/var/lib/blazen-staging/` payload now live there. See
  `docs/10-ROADMAP.md` § "M1 operational footguns" for the full log.
- **AGENTS.md / CLAUDE.md** updated to reflect this split: paul is
  the primary blazen_os rig; macOS the primary rachel rig.

### rachel — new mobile twin, fully scaffolded on macOS

Located at `/Users/beret/dev/rachel/`. **macOS Claude owns this repo**;
paul Claude should treat it as read-mostly.

- `flutter create` populated `ios/Runner/`, `android/app/`.
- `pubspec.yaml` pinned; Flutter 3.38.5 + Dart 3.10.4.
- Bundle ID: `os.blazen.jessica` (matches the URN scheme used in
  blazen_os JSON schemas).
- `lib/` scaffold:
  - `orchestrator/` — async pipeline supervisor (Dart equivalent of
    the Python orchestrator).
  - `fabric/` — `Fact`, `PeerInfo`, `SyncLog` 1:1 with the Rust crate.
  - `voice/` — `WakeWordEngine`, `AsrEngine`, `TtsEngine`, `VoiceIdEngine`
    facades over MethodChannels.
  - `intents/router.dart` — loads the **shared blazen_os YAML** and
    matches PL+EN regex triggers (Python `(?P<>)` → Dart `(?<>)`
    converted in `_dartRegex`).
  - `adapters/{gemini,email,facebook,podcasts}.dart` — adapter
    contracts (no impl yet).
  - `state/{notes,reminders,profile}.dart` — store contracts.
  - `fabric/client.dart` — TLS+TCP client contract.
- `docs/product/` symlinked to `../blazen_os/docs/product/`.
- `docs/platform-mobile/{00..08}.md` — full mobile-specific docs
  (architecture, build-and-ship, on-device ML, roadmap,
  permissions, background modes, native plugins, testing).
- Tests: **17/17 Dart green** (2 widget + 2 orchestrator + 8 fabric
  sync log + 5 intent router against shared YAML).

## Current test pyramid (cross-repo, 2026-06-11)

| Where  | Tier 0 + 1 (unit + component) | Schemas / audit |
|--------|-------------------------------|------------------|
| blazen_os Python | **64/64**                | 16 IPC schemas validated |
| blazen_os Rust   | **18/18**                | — |
| rachel Dart      | **17/17**                | — |
| Audit            | **0 errors** (TBD SHAs expected) | — |

## What paul Claude should do next

When the paul Claude session reads this and pulls the latest blazen_os
checkout, it should:

1. Read `CLAUDE.md` and confirm the operating context. It now says
   "paul is primary for blazen_os; macOS for rachel".
2. **🎉 Build #14 SUCCEEDED (2026-06-11 12:53 paul-local).**
   Artefacts:
   - `vm-images/blazen_os-0.0.1-dev.qcow2` — **2.3 GB** (for QEMU)
   - `build/pi-gen/deploy/2026-06-11-blazen_os-blazen.img` — 3.0 GB raw
   - `build/pi-gen/deploy/image_2026-06-11-blazen_os-blazen.zip` — 842 MB
   Verified contents (loopback-mounted, read-only):
   - `/usr/lib/blazen/.venv/` Python venv
   - `/usr/lib/blazen/blazend/` Python sources
   - `/usr/lib/blazen/bin/` 6 Rust aarch64 ELF binaries
     (audio-in/out, fabric, health, tts, wake)
   - `/etc/systemd/system/blazend-*.service` × 10 + `blazend.target`,
     all enabled via `blazend.target.wants/`.

3. **M1 footguns discovered (now documented in
   docs/10-ROADMAP.md):**
   - pi-gen does NOT automatically rsync `files/` from substages —
     ship a host-side `00-run.sh` that does it explicitly.
   - `SUB_STAGE_DIR` / `ROOTFS_DIR` are NOT exported to subprocess
     scripts — derive via `SUB_STAGE_DIR="$PWD"` (pi-gen pushd's)
     and `ROOTFS_DIR="${WORK_DIR}/${STAGE}/rootfs"`.
   - pi-gen chroot mounts `/tmp` as tmpfs — staging payload must go
     elsewhere (we use `/var/lib/blazen-staging/`).
   - QEMU 11 on macOS HVF only accepts `cpu=host|max|cortex-a53|a57`.
   - macOS AF_UNIX path cap ~104 chars — pytest's `tmp_path` exceeds it.
   - Docker `pigen_work` container persists between runs — rerun
     after failure needs `docker rm -v -f pigen_work`.
   - pi-gen master breaks Bookworm signatures — pinned to
     `2026-04-13-raspios-trixie-arm64`.
   - `git clone --depth 1` needs `--branch <tag>` for non-default refs.
   - `make` default PATH drops `~/.cargo/bin` — Makefile picks
     absolute path for `cross`.
   - `git rev-parse --show-toplevel` can climb to a parent repo —
     Makefile uses `pwd`.
   - Cross-compile via `cross` needs `libasound2-dev:arm64` in the
     image (handled by `crates/Cross.toml`).

4. **What's left for M1 exit criterion** (Task #40 in this repo's
   task list): boot the qcow2 in QEMU, SSH on `:2222`, verify
   `systemctl is-active blazend.target` and `/run/blazen/state.json`
   has `ready: true`. After that, M1 fully closed and M2 begins.
3. Once the pi-gen image succeeds (likely needs one more
   substage-layout fix), boot it in QEMU and confirm:
   - SSH is on by default (pubkey-only); see docs/06-SSH-BOOTSTRAP.md.
   - `blazend-orchestrator.service` is active.
   - `blazend-fabric.service` is active.
   - `/run/blazen/state.json` exists with `ready: true`.
4. M2: turn on real wake / VAD / ASR (replace the Rust mock units
   and Python mock units with real `cpal` / `ort` / `faster-whisper`
   paths).
5. Cross-repo: when changes touch `docs/product/`, push to the
   shared remote so the macOS Claude can pick them up (the symlink
   propagates automatically once macOS pulls).

## What macOS Claude does next

### 🔄 Mobile pivot: Flutter → native (2026-06-12)

Following user direction, the mobile stack moved from Flutter to
**fully native** (Swift + SwiftUI on iOS, Kotlin + Compose on
Android), with **shared Rust business core** in
`crates/jessica-core/` exposed via FFI. Reason: iOS 26+/27
features (Foundation Models, App Intents, Live Activities, Personal
Voice, BackgroundAssets) are Swift-only; Flutter wraps add quarters
of lag.

New project layout:
- `/Users/beret/dev/jessica-ios/` (shipping, SwiftUI)
- `/Users/beret/dev/jessica-android/` (shipping, Compose)
- `/Users/beret/dev/rachel/` (**reference impl** — conformance
  harness for the Rust port; not shipping; marked with
  `REFERENCE-ONLY.md`)

Shared logic in this repo:
- ✓ `crates/jessica-core/` — pure Rust, intent router +
  re-export of `blazend-fabric` types. **6/6 tests green.**
- ⧗ `crates/jessica-ffi/` — cbindgen + jni-rs (next).

Detailed plan + rationale in
[`docs/product/15-NATIVE-MIGRATION.md`](docs/product/15-NATIVE-MIGRATION.md)
and the revised
[`docs/product/09-MOBILE-PLATFORM-DECISION.md`](docs/product/09-MOBILE-PLATFORM-DECISION.md).

### Native roadmap

1. ✓ `jessica-core` Rust crate (intent router ported from Dart,
   sync log re-exported from `blazend-fabric`). 6/6 tests green.
2. ⧗ `jessica-ffi` Rust crate (cbindgen → Swift Package, jni-rs → AAR).
3. ⧗ `jessica-ios` SwiftUI shell + Foundation Models hello-world.
4. ⧗ `jessica-android` Compose shell + Gemini Nano hello-world.
5. M2: real on-device ML (Apple Speech / Google Speech / CoreML /
   TFLite).
6. M3: Apple Intelligence + Gemini Nano short-LLM path (direct, no
   Flutter MethodChannel hop).
7. M4: integrations (Gemini cloud / IMAP / Facebook).

### Status of `rachel/` (Flutter reference)

- 18/18 Dart tests still green; **stays as conformance harness**
  for the Rust port.
- README + REFERENCE-ONLY.md mark it clearly. No further feature
  development there.
- The Dart `IntentRouter` and `SyncLog` round-trip against the
  Rust port in CI (M2+) so the two implementations can't drift.

### What paul Claude needs to know

- `crates/jessica-core/` lives in this workspace now. It
  compiles + tests as part of `cargo test --workspace`. If you
  rebuild the Pi image, this crate ships nowhere new — it's mobile-
  only. But it's wired into the workspace so paul's `make test-fast`
  includes its tests.
- `docs/product/{09,15}.md` are the new spec entries. They're shared
  contract; if paul edits them, push back to macOS as usual.
- The Flutter `rachel/` is intentionally still around. Don't try to
  delete it; it's the conformance harness.

**New cross-host sync protocol** is in
`docs/16-SYNC-PROTOCOL.md` (mirror at
`../rachel/docs/platform-mobile/09-SYNC-PROTOCOL.md`). Both Claude
sessions follow it: every change that crosses the shared boundary
must update docs + sync via `make push-paul` / `make pull-paul`.
`scripts/sync-to-paul.sh` now runs `make test-fast` before pushing
and refuses to push red.

## Coordination rules (concrete)

- Anything under `blazen_os/docs/product/` is shared spec. Either
  Claude can edit it, but both should pull before editing, and both
  should mention the change in the commit message so the other side
  doesn't get stale.
- `configs/_schema/events/` is shared contract — the same wire format
  appears in both Rust (`crates/blazend-ipc/`, `crates/blazend-fabric/`)
  and Dart (`rachel/lib/fabric/`). Changes here are cross-repo by
  definition.
- `configs/intents/system.yaml` is the shared vocabulary. rachel reads
  it via `../blazen_os/configs/intents/system.yaml` (declared in
  `rachel/pubspec.yaml` assets); a new intent here needs PL+EN
  triggers and a scenario in `blazen_os/tests/scenarios/`.

## Files of note (this commit)

- `HANDOFF.md` (this file).
- `docs/product/{11..13}.md` (fabric design).
- `docs/15-DEV-WORKFLOW.md` (decision revised).
- `CLAUDE.md`, `AGENTS.md` (split rules added).
- `crates/blazend-fabric/` (new).
- `configs/fabric.yaml`, `configs/intents/system.yaml`,
  `configs/voice-policy.yaml` (fabric mutations).
- `stage-blazen/00-install/files/etc/systemd/system/*.service`
  (moved here from stage-level).
- `scripts/build-image.sh` (payload at substage level).

Both Claude sessions are tested + green at this snapshot. Build #12
is in flight on paul.
