# rpi5/Makefile — entrypoint for the blazen_os Raspberry Pi 5 appliance project.
#
# The appliance is one project inside the blazen_os monorepo. Its build is
# orchestrated from the repo root (so the shared core in ../crates and the
# appliance crates in ./crates build together, and the venv is shared). This
# Makefile just forwards the common targets up, so you can run them from inside
# rpi5/. See ../Makefile for the full target list and ../docs/14-RUST-PYTHON-SPLIT.md.

ROOT := $(realpath $(dir $(lastword $(MAKEFILE_LIST)))/..)

.PHONY: help build rust rust-aarch64 python venv gen-events models dev \
        vm-image pi-image pi-image-dev run-vm test test-fast test-vm \
        test-scenario audio-fixtures audit clean distclean
help build rust rust-aarch64 python venv gen-events models dev \
vm-image pi-image pi-image-dev run-vm test test-fast test-vm \
test-scenario audio-fixtures audit clean distclean:
	$(MAKE) -C "$(ROOT)" $@
