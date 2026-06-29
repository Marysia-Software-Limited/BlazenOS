# Makefile — Jessica iOS dev workflow.
#
# Requires (one-time setup on the maintainer's mac):
#   brew install xcodegen                 # project generator (optional)
#   # Xcode (or Xcode-beta) installed under /Applications.
#   # Auto-picks Xcode-beta if found, else /Applications/Xcode.app.
#
# Typical loop:
#   make project       # regen Jessica.xcodeproj from project.yml
#   make build         # xcodebuild for the simulator
#   make test          # JessicaCoreTests via swift test (no full Xcode UI)
#   make debug         # open the project in Xcode
#   make clean

SCHEME ?= Jessica
DESTINATION ?= platform=iOS Simulator,name=iPhone 16
DERIVED ?= build

# Pick an Xcode that ships swift-package; the bare CommandLineTools
# install on a fresh mac sometimes has a broken swift-package binary.
DEVDIR := $(shell ls -d /Applications/Xcode-beta.app/Contents/Developer 2>/dev/null \
           || ls -d /Applications/Xcode.app/Contents/Developer 2>/dev/null \
           || echo /Library/Developer/CommandLineTools)
SWIFT := $(DEVDIR)/Toolchains/XcodeDefault.xctoolchain/usr/bin/swift
XCODEBUILD := $(DEVDIR)/usr/bin/xcodebuild
export DEVELOPER_DIR := $(DEVDIR)

.PHONY: project build test test-core debug clean ffi format check require-xcodegen

project: require-xcodegen
	xcodegen generate

build: project
	$(XCODEBUILD) -scheme "$(SCHEME)" -destination '$(DESTINATION)' \
	    -derivedDataPath $(DERIVED) build

# Run the pure-Swift JessicaCore tests without Xcode. Faster than the
# full simulator build, and works on hosts with only CommandLineTools.
test: test-core

test-core:
	cd JessicaCore && $(SWIFT) test

debug: project
	open Jessica.xcodeproj

clean:
	rm -rf $(DERIVED) DerivedData Jessica.xcodeproj .build JessicaCore/.build

# M1: rebuild jessica_ffi.xcframework from the monorepo's Rust workspace.
# See docs/build.md for the cargo + cbindgen + lipo + xcodebuild -create-xcframework
# pipeline. Stubbed for M0.
ffi:
	@echo "M0 stub. M1: produce JessicaFFI.xcframework from domains/jessica-ffi."

require-xcodegen:
	@which xcodegen >/dev/null || { echo "Install: brew install xcodegen"; exit 1; }
