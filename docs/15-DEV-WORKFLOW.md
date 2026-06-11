# 15 — Dev workflow (Linux vs macOS)

`blazen_os` targets Raspberry Pi 5 (aarch64 Linux). The full image build
needs Linux + Docker, but many tasks — quick iterations on the Python
orchestrator, IPC contract changes, voice scenarios — run fine on macOS.
This doc fixes the **canonical hybrid workflow**.

> **Decision (2026-06-11, revised):** **Linux** (`paul` Arch box) is the
> **primary** development environment for **`blazen_os` only**. **macOS**
> is the **primary** environment for **`rachel`** (the mobile twin). Each
> repo gets its own Claude Code session: paul ↔ blazen_os, macOS ↔ rachel.
> The shared product spec under `docs/product/` is co-edited by both
> sides. The "secondary" use of each rig (paul for rachel, macOS for
> blazen_os) is fine for reading the other's repo but not for committing
> changes.

## 1. Why Linux is primary

- **pi-gen needs Linux + Docker.** The whole SD-image pipeline is
  Linux-only. Doing it on macOS via colima/Docker Desktop works in
  theory but doubles the debug surface.
- **Cross-compile for aarch64 is friction-free.** `cargo install cross`
  + Docker = working `aarch64-unknown-linux-gnu` build in seconds. On
  macOS the same workflow needs colima first.
- **`cpal` / `alsa-sys` link to native ALSA**, which exists upstream
  only on Linux. Cross via `cross` solves it, but staying on Linux
  removes the indirection.
- **Target parity.** The closer your dev environment to the deployment
  target, the fewer "works for me" bugs.
- **CI lives here.** GitHub Actions / self-hosted runners are Linux —
  if `make test-fast` doesn't pass on Linux, the contributor
  experience breaks regardless of what passes locally.

## 2. What macOS is still good for

- **Inner-loop iteration on the orchestrator + Python code.** `make dev`
  brings the whole stack up on macOS without a VM. Perfect for tweaking
  intent routing, IPC handlers, state-machine logic, scenarios.
- **Quick visual checks.** Browsing logs, editing YAML configs,
  hand-running `pytest`, `cargo test`, `python -m blazend.<unit>`.
- **`make qemu-smoke`** confirms `qemu-system-aarch64` + HVF acceleration
  + `virt` machine work — useful as a sanity gate before kicking a
  long build on the Linux box.

## 3. The canonical hybrid loop

```
┌────────────────────────────┐                   ┌──────────────────────────┐
│  macOS workstation         │  rsync / git push │  paul (Arch Linux)       │
│                            ├──────────────────▶│                          │
│  - edit code / docs        │                   │  - make build            │
│  - make test-fast          │                   │  - make test-fast        │
│  - make dev (quick smoke)  │                   │  - make rust-aarch64     │
│  - make qemu-smoke         │                   │  - make vm-image         │
│  - git commit              │                   │  - make run-vm           │
│                            │  artefacts ◀──────│  - real Pi 5 SD flash    │
└────────────────────────────┘                   └──────────────────────────┘
        primary editor                              primary build + test rig
```

### Practical commands

**On macOS — quick iteration:**

```bash
make build              # cargo build (host) + python venv
make test-fast          # 57 Python + 6 Rust tests; <2s
make dev                # full mock stack with state.json live in /tmp/blazen-501/
make qemu-smoke         # gate: HVF + virt machine + cpu host
```

**On Linux (paul) — build the bits that actually ship:**

```bash
make build              # same
make test-fast          # same
make rust-aarch64       # produces 5 aarch64 ELF binaries via cross + Docker
make vm-image           # full pi-gen pipeline → vm-images/*.qcow2  (15-30 min)
make run-vm             # boots that image in QEMU
make pi-image           # raw .img for `dd` onto an SD card
```

### Syncing the tree

Two options, pick by taste:

1. **git** (recommended for shared work). `git push origin <branch>`
   on macOS, `git pull` on paul. History travels with the code.
2. **rsync** (fast local iteration). When you don't want to commit
   every two minutes:

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
