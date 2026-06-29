#!/usr/bin/env bash
# Build jessica_ffi.xcframework from the Rust crate.
#
# Target slices:
#   - aarch64-apple-ios          (iOS device, arm64)
#   - aarch64-apple-ios-sim      (Apple Silicon simulator)
#   - x86_64-apple-ios           (Intel simulator)        — skipped if not installed
#
# Output:
#   <repo>/build/jessica-ffi/JessicaFFI.xcframework
#   …also rsync'd into /Users/beret/dev/ios/JessicaCore/Frameworks/
#   when that path exists.
#
# Requires (one-time):
#   rustup target add aarch64-apple-ios aarch64-apple-ios-sim x86_64-apple-ios
#   xcode-select -s /Applications/Xcode.app/Contents/Developer  # or Xcode-beta
#
# Exit code 2 == toolchain unavailable (CI-friendly skip signal).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CRATE="$ROOT/domains/jessica-ffi"
BUILD="$ROOT/build/jessica-ffi"
HEADER="$CRATE/include/jessica_ffi.h"

# Pick a developer dir: Xcode-beta first, then stable, then CommandLineTools.
DEVDIR="${DEVELOPER_DIR:-}"
if [[ -z "$DEVDIR" ]]; then
    for candidate in \
        /Applications/Xcode-beta.app/Contents/Developer \
        /Applications/Xcode.app/Contents/Developer; do
        [[ -d "$candidate" ]] && { DEVDIR="$candidate"; break; }
    done
fi
if [[ -z "$DEVDIR" || ! -d "$DEVDIR" ]]; then
    echo "ERROR: full Xcode (or Xcode-beta) is required for xcframework packaging." >&2
    exit 2
fi
export DEVELOPER_DIR="$DEVDIR"

# Required targets — fail loudly if missing.
required=(aarch64-apple-ios aarch64-apple-ios-sim)
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

# x86_64-apple-ios is optional (Intel-mac simulators).
HAS_X64_SIM=0
grep -q '^x86_64-apple-ios$' <<<"$installed" && HAS_X64_SIM=1

mkdir -p "$BUILD"

build_target () {
    local target="$1"
    echo "→ cargo build --release --target $target"
    cargo build --manifest-path "$CRATE/Cargo.toml" --release --target "$target"
}

build_target aarch64-apple-ios
build_target aarch64-apple-ios-sim
(( HAS_X64_SIM == 1 )) && build_target x86_64-apple-ios

DEV_LIB="$ROOT/domains/target/aarch64-apple-ios/release/libjessica_ffi.a"
SIM_ARM_LIB="$ROOT/domains/target/aarch64-apple-ios-sim/release/libjessica_ffi.a"
SIM_X64_LIB="$ROOT/domains/target/x86_64-apple-ios/release/libjessica_ffi.a"

# Lipo the simulator slices into a single .a when both are present.
SIM_LIB="$BUILD/libjessica_ffi-sim.a"
if (( HAS_X64_SIM == 1 )); then
    echo "→ lipo simulator slices"
    lipo -create "$SIM_ARM_LIB" "$SIM_X64_LIB" -output "$SIM_LIB"
else
    cp "$SIM_ARM_LIB" "$SIM_LIB"
fi

# Module map next to the header — Swift requires this for binary frameworks.
HEADERS_DIR="$BUILD/Headers"
mkdir -p "$HEADERS_DIR"
cp "$HEADER" "$HEADERS_DIR/jessica_ffi.h"
cat > "$HEADERS_DIR/module.modulemap" <<EOF
module JessicaFFI {
    header "jessica_ffi.h"
    link "jessica_ffi"
    export *
}
EOF

# Pack the xcframework.
XCFW="$BUILD/JessicaFFI.xcframework"
rm -rf "$XCFW"
echo "→ xcodebuild -create-xcframework"
xcodebuild -create-xcframework \
    -library "$DEV_LIB" -headers "$HEADERS_DIR" \
    -library "$SIM_LIB" -headers "$HEADERS_DIR" \
    -output "$XCFW"

# Vendor into the iOS Swift Package when the path exists.
IOS_VENDOR="/Users/beret/dev/ios/JessicaCore/Frameworks"
if [[ -d "/Users/beret/dev/ios/JessicaCore" ]]; then
    mkdir -p "$IOS_VENDOR"
    rsync -a --delete "$XCFW/" "$IOS_VENDOR/JessicaFFI.xcframework/"
    echo "→ vendored to $IOS_VENDOR/JessicaFFI.xcframework"
fi

echo "✓ JessicaFFI.xcframework ready at $XCFW"
