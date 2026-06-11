#!/usr/bin/env bash
# Cross-compile jessica-ffi for every Android ABI we ship and drop
# the resulting .so files into the Android project's jniLibs/.
#
# Targets:
#   aarch64-linux-android   → jniLibs/arm64-v8a/libjessica_ffi.so
#   armv7-linux-androideabi → jniLibs/armeabi-v7a/libjessica_ffi.so
#   x86_64-linux-android    → jniLibs/x86_64/libjessica_ffi.so
#
# Requires:
#   rustup target add aarch64-linux-android armv7-linux-androideabi x86_64-linux-android
#   ANDROID_HOME or ANDROID_SDK_ROOT pointing at the Android SDK.
#   NDK installed under $ANDROID_HOME/ndk/<version>/ — newest wins.
#
# Exit code 2 == toolchain unavailable (CI-friendly skip signal).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CRATE="$ROOT/crates/jessica-ffi"
ANDROID_PROJECT="${ANDROID_PROJECT:-/Users/beret/dev/android}"

SDK="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-$HOME/Library/Android/sdk}}"
if [[ ! -d "$SDK/ndk" ]]; then
    echo "ERROR: no NDK under $SDK/ndk" >&2
    exit 2
fi

# Pick the newest NDK directory by version.
NDK_VERSION=$(ls "$SDK/ndk" | sort -V | tail -1)
NDK="$SDK/ndk/$NDK_VERSION"
HOST_TAG="darwin-x86_64"   # NDK ships universal toolchain under this dir name.
TOOLCHAIN="$NDK/toolchains/llvm/prebuilt/$HOST_TAG"
if [[ ! -d "$TOOLCHAIN" ]]; then
    echo "ERROR: missing NDK toolchain at $TOOLCHAIN" >&2
    exit 2
fi

API_LEVEL="${ANDROID_API_LEVEL:-30}"   # matches app/build.gradle.kts minSdk

# Map rust target → (clang prefix, ABI dir, linker env var name)
configure_target () {
    local rust_target="$1"
    case "$rust_target" in
        aarch64-linux-android)
            CLANG_PREFIX="aarch64-linux-android"
            ABI_DIR="arm64-v8a"
            LINKER_VAR=CARGO_TARGET_AARCH64_LINUX_ANDROID_LINKER
            CC_VAR=CC_aarch64_linux_android
            AR_VAR=AR_aarch64_linux_android
            ;;
        armv7-linux-androideabi)
            CLANG_PREFIX="armv7a-linux-androideabi"
            ABI_DIR="armeabi-v7a"
            LINKER_VAR=CARGO_TARGET_ARMV7_LINUX_ANDROIDEABI_LINKER
            CC_VAR=CC_armv7_linux_androideabi
            AR_VAR=AR_armv7_linux_androideabi
            ;;
        x86_64-linux-android)
            CLANG_PREFIX="x86_64-linux-android"
            ABI_DIR="x86_64"
            LINKER_VAR=CARGO_TARGET_X86_64_LINUX_ANDROID_LINKER
            CC_VAR=CC_x86_64_linux_android
            AR_VAR=AR_x86_64_linux_android
            ;;
        *) echo "unknown target: $rust_target" >&2; return 1 ;;
    esac
    CLANG_BIN="$TOOLCHAIN/bin/${CLANG_PREFIX}${API_LEVEL}-clang"
    AR_BIN="$TOOLCHAIN/bin/llvm-ar"
    if [[ ! -x "$CLANG_BIN" ]]; then
        echo "ERROR: missing NDK clang at $CLANG_BIN" >&2
        exit 2
    fi
    export "$LINKER_VAR"="$CLANG_BIN"
    export "$CC_VAR"="$CLANG_BIN"
    export "$AR_VAR"="$AR_BIN"
}

build_target () {
    local rust_target="$1"
    configure_target "$rust_target"
    echo "→ cargo build --release --target $rust_target"
    cargo build --manifest-path "$CRATE/Cargo.toml" --release --target "$rust_target"

    local out_dir
    if [[ -d "$ANDROID_PROJECT/app/src/main/jniLibs" ]]; then
        out_dir="$ANDROID_PROJECT/app/src/main/jniLibs/$ABI_DIR"
    else
        out_dir="$ROOT/build/jessica-ffi/jniLibs/$ABI_DIR"
    fi
    mkdir -p "$out_dir"
    cp "$ROOT/crates/target/$rust_target/release/libjessica_ffi.so" "$out_dir/"
    echo "  → $out_dir/libjessica_ffi.so"
}

# Required targets.
required=(aarch64-linux-android armv7-linux-androideabi x86_64-linux-android)
installed=$(rustup target list --installed 2>/dev/null)
missing=()
for t in "${required[@]}"; do
    grep -q "^${t}\$" <<<"$installed" || missing+=("$t")
done
if (( ${#missing[@]} > 0 )); then
    echo "ERROR: install missing rust targets:" >&2
    printf '  rustup target add %s\n' "${missing[@]}" >&2
    exit 2
fi

for t in "${required[@]}"; do
    build_target "$t"
done

echo "✓ jniLibs populated for arm64-v8a / armeabi-v7a / x86_64"
