# Makefile — Jessica Android dev workflow.
#
# Bootstrap (one-time, if `gradle` is not on PATH):
#   brew install gradle              # or: download from gradle.org/releases
#   make wrapper                     # creates gradlew + wrapper jar in-tree
#
# Typical loop:
#   make build                       # ./gradlew assembleDebug
#   make test                        # ./gradlew :core:test
#   make install                     # adb install -r app/build/.../app-debug.apk
#   make debug                       # ./gradlew installDebug + am start
#   make lint                        # ./gradlew lint
#   make clean

# --- Toolchain resolution ------------------------------------------------

SDK ?= $(HOME)/Library/Android/sdk
JAVA_HOME_BREW := /opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home
ifneq ("$(wildcard $(JAVA_HOME_BREW))","")
  export JAVA_HOME := $(JAVA_HOME_BREW)
endif

GRADLE_VERSION ?= 8.10.2

GRADLEW := $(CURDIR)/gradlew
SYSTEM_GRADLE := $(shell command -v gradle 2>/dev/null)
GRADLE := $(if $(wildcard $(GRADLEW)),$(GRADLEW),$(SYSTEM_GRADLE))

# --- Targets -------------------------------------------------------------

.PHONY: build test test-core lint install debug clean local-properties wrapper toolchain require-gradle

build: local-properties require-gradle
	$(GRADLE) assembleDebug

test: test-core

test-core: local-properties require-gradle
	$(GRADLE) :core:test

lint: local-properties require-gradle
	$(GRADLE) lint

install: build
	adb install -r app/build/outputs/apk/debug/app-debug.apk

debug: local-properties require-gradle
	$(GRADLE) installDebug
	adb shell am start -n os.blazen.jessica/.MainActivity

clean:
	$(GRADLE) clean 2>/dev/null || true
	rm -rf .gradle build app/build core/build

# --- Bootstrap helpers ---------------------------------------------------

local-properties:
	@if [ ! -f local.properties ]; then \
		echo "Writing local.properties (sdk.dir=$(SDK))"; \
		echo "sdk.dir=$(SDK)" > local.properties; \
	fi

wrapper: require-gradle
	$(SYSTEM_GRADLE) wrapper --gradle-version=$(GRADLE_VERSION) --distribution-type=bin

require-gradle:
	@if [ -z "$(GRADLE)" ]; then \
		echo "ERROR: no gradle found. brew install gradle, or run \`make wrapper\` once."; \
		exit 1; \
	fi
