# 15 — Dev workflow (paul-primary monorepo)

`blazen_os` is a **monorepo** with three surfaces:

- The Raspberry Pi 5 appliance (aarch64 Linux).
- The native Android app ([`android/`](../android/)) — Kotlin + Compose.
- The native iOS app ([`ios/`](../ios/)) — Swift + SwiftUI.

Plus the shared Rust mobile core ([`crates/jessica-core`](../crates/jessica-core/),
[`crates/jessica-ffi`](../crates/jessica-ffi/)).

This doc fixes the **canonical workflow** across all three surfaces.

> **Decision (2026-06-11, revised 2026-06-11):** **Linux** (`paul` Arch
> box) is the **primary** rig for the whole monorepo — Pi 5 builds and
> tests, Android gradle + adb, Rust core, all docs and shared specs. The
> maintainer's Mac is required only for the final iOS xcodebuild and
> TestFlight cut. The Flutter prototype at `../rachel/` is a reference,
> not a shipping target; it stays editable from either rig but is no
> longer the canonical mobile dev surface.

## 1. Why Linux (paul) is primary

- **pi-gen needs Linux + Docker.** The whole SD-image pipeline is
  Linux-only.
- **Cross-compile for aarch64 is friction-free.** `cargo install cross`
  + Docker = working `aarch64-unknown-linux-gnu` build in seconds.
- **`cpal` / `alsa-sys` link to native ALSA**, which exists upstream
  only on Linux.
- **Target parity.** The closer your dev environment to the deployment
  target, the fewer "works for me" bugs.
- **CI lives here.** GitHub Actions / self-hosted runners are Linux.
- **Android builds run anywhere with a JDK** — paul covers that
  natively (`./gradlew assembleDebug` + `adb install`).
- **Rust mobile core** (`jessica-core`, `jessica-ffi`) is the
  same workspace as the Pi 5 crates — paul builds + tests it the same
  way it builds the Pi 5 binaries.

## 2. When the Mac is required

- **Final iOS build / signing / TestFlight.** `xcodebuild` and the
  iOS simulator only exist on macOS.
- **Personal Voice / Apple Intelligence end-to-end smoke.** On-device
  feature smoke tests need an actual iPhone running iOS 17+ (18.4+
  for Foundation Models).
- **Anything else iOS-related can happen on paul:** editing Swift
  sources, `project.yml`, `JessicaCore` tests (via `swift build` if
  swift is installed on Linux, or by porting the matching tests to
  the Kotlin twin and validating cross-language).

## 3. The canonical loop

```
┌──────────────────────────┐                  ┌─────────────────────────┐
│  paul (Arch Linux)       │                  │  maintainer's Mac       │
│  primary monorepo rig    │   git push/pull  │  iOS cut + TestFlight   │
│                          ├─────────────────▶│                         │
│  Pi 5:                   │                  │  - xcodebuild           │
│   - make build           │                  │  - sign + archive       │
│   - make test-fast       │                  │  - upload to TestFlight │
│   - make rust-aarch64    │                  │  - on-device smoke      │
│   - make vm-image        │                  │                         │
│   - make run-vm          │                  │                         │
│   - make pi-image        │                  │                         │
│  Android:                │                  │                         │
│   - cd android && make build / test / install                        │
│  iOS (sources only):     │                  │                         │
│   - edit Swift + yml + docs                                          │
│   - cargo test for jessica-core + jessica-ffi                 │
└──────────────────────────┘                  └─────────────────────────┘
```

### Practical commands

**On paul — everything except the iOS cut:**

```bash
# Pi 5
make build              # cargo build (host) + python venv
make test-fast          # 57 Python + 6 Rust tests; <2s
make rust-aarch64       # produces aarch64 ELF binaries via cross + Docker
make vm-image           # full pi-gen pipeline → vm-images/*.qcow2  (15-30 min)
make run-vm             # boots that image in QEMU
make pi-image           # raw .img for `dd` onto an SD card

# Android
cd android/
make build              # ./gradlew assembleDebug
make test               # :core JVM tests
make install            # adb install -r app-debug.apk

# iOS (sources, docs, Rust core — no Xcode)
cd ios/
# edit Swift sources / project.yml / docs
cargo test -p jessica-core -p jessica-ffi   # from monorepo root
```

**On the Mac — the iOS cut only:**

```bash
cd ios/
make project            # regen Jessica.xcodeproj from project.yml
make test               # JessicaCoreTests via swift test
make build              # xcodebuild for the iPhone 16 simulator
make debug              # open Jessica.xcodeproj
# then: signing → TestFlight (manual for M1)
```

### Syncing the tree

**git via GitHub is the canonical transport** (revised 2026-06-11). The
hub is `git@github.com:Marysia-Software-Limited/BlazenOS.git` (branch
`main`); paul and rachel are both clones. Full protocol in
[`16-SYNC-PROTOCOL.md`](16-SYNC-PROTOCOL.md).

```bash
make sync-pull      # git pull --ff-only origin main   (start of session)
git commit -am '…'  # commit your work
make sync-push      # runs test-fast, then git push origin main
make rachel-pull    # (from paul) ssh rachel and fast-forward it
```

History travels with the code; conflicts are resolved by git, not by
mtime. **rsync is deprecated** and retained only for bulk build-artifact
transfer (qcow2/img are gitignored). The legacy one-liner, if you ever
need it:

```bash
rsync -avz --delete \
  --exclude='.venv/' \
  --exclude='crates/target/' \
  --exclude='vm-images/' \
  --exclude='models/' \
  --exclude='_test_projects/' \
  --exclude='build/' \
  --exclude='vm-runs/' \
  --exclude='.idea/' \
  --exclude='.pytest_cache/' \
  --exclude='.ruff_cache/' \
  --exclude='.DS_Store' \
  --exclude='__pycache__/' \
  --exclude='*.egg-info/' \
  ./ paul:~/dev/blazen_os/
```

A `scripts/sync-to-paul.sh` wrapping the above is in flight; M1.

## 4. Where each tool actually lives

| Tool                | Linux | macOS | Notes |
|---------------------|:-----:|:-----:|-------|
| Python venv + pytest | ✓    | ✓     | parity 1:1 |
| cargo build (host)   | ✓    | ✓     | parity 1:1 |
| cargo cross-compile  | ✓ (native + cross) | ✓ (cross + colima) | Linux simpler |
| pi-gen image build   | ✓    | ✗ (or colima) | Linux primary |
| make qemu-smoke      | ✓ (KVM) | ✓ (HVF) | both fine |
| make run-vm          | ✓ (KVM, raspi4b) | ⧗ (HVF, virt only — raspi4b is x86-host-only in QEMU upstream) | Linux full path |
| ReSpeaker HAT bring-up | ✓ (with USB passthrough) | ⧗ | physical Pi recommended |
| Real Pi 5 flash      | ✓ (`/dev/sdX`) | ✓ (`/dev/diskN`) | platform-specific cmd |

## 5. CI shape (M2+)

CI is a single self-hosted runner on `paul` plus GitHub Actions for
the cheap stuff:

| Job                | Tier | Runner               | Cadence |
|--------------------|------|----------------------|---------|
| `lint-format`      | 0    | GH Actions (Linux)   | every PR |
| `python-unit`      | 0    | GH Actions (Linux)   | every PR |
| `rust-unit`        | 0    | GH Actions (Linux)   | every PR |
| `schema-roundtrip` | 0    | GH Actions           | every PR |
| `component`        | 1    | GH Actions           | every PR |
| `rust-aarch64`     | 1    | `paul` (Docker)      | every PR |
| `vm-image`         | 2    | `paul`               | every push to main |
| `qemu-scenarios`   | 3    | `paul`               | every push to main |
| `hardware-pi5`     | 4    | manual (the Pi 5)    | weekly |
| `soak-24h`         | 5    | `paul` + Pi 5        | weekly |

## 6. Frequently-skipped path landmines

- **`git rev-parse --show-toplevel`** finds the **outermost** repo
  containing the cwd. If you have multiple repos nested under one
  parent (`~/dev/.git` and `~/dev/blazen_os/.git`), `make` may pick
  the wrong one. The Makefile uses `pwd` instead.
- **`~/.cargo/bin`** is not on `make`'s default PATH. The Makefile
  prefers the absolute `~/.cargo/bin/cross` path.
- **macOS QEMU with HVF** only accepts CPU types `host`, `max`,
  `cortex-a53`, `cortex-a57`. Use `host` (auto-detected by
  `qemu-smoke.sh`).
- **macOS AF_UNIX path cap** is ~104 chars; pytest's `tmp_path`
  exceeds it. Tests use a short `/tmp/bl-*` tempdir.
- **`make models` HTTP errors** are non-fatal — the model bundle is
  optional at build time; the device lazy-loads on first wake. The
  installer logs HTTP errors as warnings and continues.

## 7. PL TL;DR

- **Linux (paul) jest głównym środowiskiem developerskim** od M1+.
- macOS zostaje do szybkiej iteracji w `make dev` i `make test-fast`.
- Synchronizacja: git + okazjonalny `rsync` z `--exclude` na build
  artefacts.
- pi-gen, cross-compile, vm-image, run-vm, hardware-in-the-loop —
  wszystko żyje na paulu.
- CI to self-hosted runner na paulu plus GitHub Actions dla testów
  unit/component.
