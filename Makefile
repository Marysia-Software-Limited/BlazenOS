# blazen_os — top-level Makefile
#
# Goals (in declared order of usefulness):
#   make help              # list targets
#   make install-deps      # host dependencies (qemu, python venv, rust, ...)
#   make build             # python venv + cargo build (host arch)
#   make rust              # cargo build --release (host arch)
#   make rust-aarch64      # cross-compile for Pi 5 (aarch64-unknown-linux-gnu)
#   make python            # python venv + deps only
#   make gen-events        # regenerate IPC event types (Python + Rust) from JSON Schemas
#   make models            # download + verify all on-device ML models
#   make dev               # run the full stack on the dev host (no VM)
#   make vm-image          # build the SD-card image as qcow2 for QEMU
#   make pi-image          # build the SD-card image as .img for `dd`
#   make qemu-setup        # download Raspberry Pi OS Lite + extract kernel/DTB
#   make run-vm            # boot the qcow2 image in QEMU with virtual audio
#   make flash DEVICE=...  # write the .img to a real SD card (guarded)
#   make test              # Tier 0..3 (unit + component + VM scenarios)
#   make test-fast         # Tier 0..1 only (cargo test + pytest)
#   make test-vm           # Tier 2..3 only
#   make test-scenario S=NAME   # single scenario from tests/scenarios/
#   make test-soak         # 24h scenario loop (long-running)
#   make audio-fixtures    # synth all WAV inputs via Piper TTS
#   make audit             # security/safety lint of configs + image
#   make clean             # remove build artifacts (NOT models)
#   make distclean         # also remove models, fixtures, vm-images, target/
#
# All paths are relative to the repo root. No assumption of being run on a Pi.

# REPO_ROOT: pwd, not `git rev-parse`, because some users have a parent
# git repo that would otherwise capture us.
REPO_ROOT      := $(shell pwd)
VENV           := $(REPO_ROOT)/.venv
PY             := $(VENV)/bin/python
PIP            := $(VENV)/bin/pip
QEMU           := qemu-system-aarch64
CARGO          := cargo
# Prefer `~/.cargo/bin/cross` (rustup install location) over a bare name,
# because `make`'s default PATH usually drops it. Falls back to plain
# `cross` if installed system-wide.
CROSS          := $(shell command -v $(HOME)/.cargo/bin/cross 2>/dev/null || echo cross)
RUST_TARGET    ?= aarch64-unknown-linux-gnu
SCENARIO       ?= 01-wake-word
DEVICE         ?=
IMAGE_NAME     ?= blazen_os
IMAGE_VERSION  ?= 0.0.1-dev
VM_IMAGE       := $(REPO_ROOT)/vm-images/$(IMAGE_NAME)-$(IMAGE_VERSION).qcow2
PI_IMAGE       := $(REPO_ROOT)/vm-images/$(IMAGE_NAME)-$(IMAGE_VERSION).img

.DEFAULT_GOAL := help

.PHONY: help
help:
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

# -------- host setup --------

.PHONY: install-deps
install-deps: ## Install host toolchain (qemu, python venv, rust, pi-gen prereqs)
	./scripts/install-deps.sh

$(VENV)/bin/python:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip wheel

.PHONY: venv python
venv python: $(VENV)/bin/python ## Create the Python virtualenv and install deps
	$(PIP) install -e ".[dev]"

# -------- build --------

.PHONY: build
build: python rust ## Build both Python (venv) and Rust (host arch)

.PHONY: rust
rust: ## Build all Rust crates for the host (cargo build --release)
	cd crates && $(CARGO) build --release --workspace

.PHONY: rust-aarch64
rust-aarch64: ## Cross-build Rust crates for Pi 5 (aarch64-unknown-linux-gnu)
	@if [ -x "$(CROSS)" ] || command -v "$(CROSS)" >/dev/null 2>&1; then \
	  echo "Using $(CROSS) for aarch64 cross-build"; \
	  cd crates && "$(CROSS)" build --release --workspace --target $(RUST_TARGET); \
	else \
	  echo "cross not found at $(CROSS); falling back to cargo (needs ALSA aarch64 sysroot)"; \
	  cd crates && $(CARGO) build --release --workspace --target $(RUST_TARGET); \
	fi

# -------- IPC events code-gen --------

.PHONY: gen-events
gen-events: python ## Regenerate IPC event types (Python + Rust) from JSON Schemas
	$(PY) scripts/gen-event-types.py --schemas configs/_schema/events \
	  --python-out src/blazend/events/_generated.py \
	  --rust-out  crates/blazend-ipc/src/events/_generated.rs

# -------- model wrangling --------

.PHONY: models
models: venv ## Download + verify all on-device ML models
	$(PY) scripts/install_models.py --config configs/llm.yaml --config configs/asr.yaml --config configs/tts.yaml --config configs/wake-word.yaml

# -------- dev-host launcher (no VM) --------

.PHONY: dev
dev: build ## Run the full blazend stack on the dev host (no VM; fastest iteration)
	./scripts/dev-run.sh

.PHONY: sync-paul push-paul
sync-paul push-paul: ## Rsync source tree to paul (refuses if tests are red; --force to override)
	./scripts/sync-to-paul.sh

.PHONY: pull-paul
pull-paul: ## Rsync paul's blazen_os changes back to this host
	./scripts/sync-from-paul.sh

.PHONY: paul-test-fast
paul-test-fast: push-paul ## Sync + run test-fast on paul
	ssh $${BLAZEN_SYNC_HOST:-paul} "cd ~/dev/blazen_os && make test-fast"

.PHONY: paul-vm-image
paul-vm-image: push-paul ## Sync + build the qcow2 image on paul (long-running)
	ssh $${BLAZEN_SYNC_HOST:-paul} "cd ~/dev/blazen_os && make vm-image"

# -------- QEMU + image build --------

.PHONY: qemu-smoke
qemu-smoke: ## Verify qemu-system-aarch64 + virt machine work on this host
	./scripts/qemu-smoke.sh

.PHONY: qemu-setup
qemu-setup: ## Download Raspberry Pi OS Lite + extract kernel/DTB for QEMU boot
	./scripts/setup-qemu-env.sh

.PHONY: vm-image
vm-image: venv rust-aarch64 ## Build the qcow2 DEV image for QEMU (login blazen + ssh on). Models lazy-loaded; run `make models` first to pre-bundle.
	./scripts/build-image.sh --format qcow2 --out $(VM_IMAGE) --dev

.PHONY: pi-image
pi-image: venv rust-aarch64 ## Build the RELEASE .img for `dd` to SD (blazen nologin + ssh off). See vm-image for a dev build.
	./scripts/build-image.sh --format raw --out $(PI_IMAGE)

.PHONY: pi-image-dev
pi-image-dev: venv rust-aarch64 ## Build a DEV .img for SD (login blazen + ssh on) — for on-hardware bring-up before the voice path exists.
	./scripts/build-image.sh --format raw --out $(PI_IMAGE) --dev

.PHONY: flash
flash: ## Flash the .img to DEVICE=/dev/diskN (guarded, prompts for confirm)
	@if [ -z "$(DEVICE)" ]; then echo "Usage: make flash DEVICE=/dev/diskN"; exit 1; fi
	./scripts/flash-sd.sh --image $(PI_IMAGE) --device $(DEVICE)

.PHONY: run-vm
run-vm: ## Boot the qcow2 image in QEMU with virtual audio
	./scripts/run-vm.sh --image $(VM_IMAGE) --config configs/vm/qemu-raspi.yaml

# -------- testing --------

.PHONY: test
test: test-fast test-vm ## Full pyramid (Tier 0..3)

.PHONY: test-fast
test-fast: venv ## Tier 0 (unit) + Tier 1 (component, mocked) — Python AND Rust
	$(PY) -m pytest tests/unit tests/component -x --tb=short
	cd crates && $(CARGO) test --workspace --quiet

.PHONY: test-vm
test-vm: venv ## Tier 2 (pipeline in VM) + Tier 3 (scenarios)
	$(PY) tests/tools/e2e-runner.py --all --image $(VM_IMAGE)

.PHONY: test-scenario
test-scenario: venv ## Run one scenario: make test-scenario S=01-wake-word
	$(PY) tests/tools/e2e-runner.py --scenario tests/scenarios/$(SCENARIO).yaml --image $(VM_IMAGE)

.PHONY: test-soak
test-soak: venv ## Tier 5: 24-hour scenario loop with telemetry
	$(PY) tests/tools/e2e-runner.py --soak 24h --image $(VM_IMAGE)

.PHONY: audio-fixtures
audio-fixtures: venv ## Synthesise all WAV inputs from scenario YAMLs (via Piper)
	$(PY) tests/tools/synth-audio.py --scenarios tests/scenarios --out tests/fixtures/audio

# -------- safety / hygiene --------

.PHONY: audit
audit: venv ## Lint configs, scan deps, dry-run firewall rules
	$(PY) scripts/audit.py

.PHONY: clean
clean: ## Remove build artifacts (keeps models + fixtures)
	rm -rf vm-images _test_projects build dist .pytest_cache .ruff_cache
	cd crates && $(CARGO) clean || true
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

.PHONY: distclean
distclean: clean ## Also remove models, audio fixtures, the venv, and Rust target
	rm -rf models tests/fixtures/audio $(VENV) crates/target
