# Build & toolchain

## Prerequisites

| Tool                | Version              | Install                                              |
|---------------------|----------------------|------------------------------------------------------|
| macOS               | 14.5+                | required for Xcode 16                                |
| Xcode               | 16.0+ (16-beta OK)   | https://developer.apple.com/xcode/                   |
| XcodeGen            | 2.39+                | `brew install xcodegen`                              |
| Swift (CLI)         | 6.0 (bundled w/ Xcode) | comes with Xcode toolchain                          |
| Rust + cbindgen (M1)| stable + cbindgen 0.27 | `cargo install cbindgen`                            |

## One-time setup

```bash
cd ios/
brew install xcodegen
make project                 # writes Jessica.xcodeproj
```

## Build matrix

```bash
make project       # regen Jessica.xcodeproj from project.yml
make test          # JessicaCoreTests via `swift test` (no Xcode UI)
make build         # xcodebuild for the iPhone 16 simulator
make debug         # open Jessica.xcodeproj
make clean         # nuke DerivedData + .build + Jessica.xcodeproj
```

## On paul (Linux)

Most of these targets need a Mac. On paul you can still:

- Edit Swift / project.yml / docs.
- Run `cargo test -p jessica-core -p jessica-ffi` to verify the
  Rust core that `JessicaCore` will wrap in M1.

The Mac drives final build / signing / TestFlight.

## M1: building `JessicaFFI.xcframework`

From the **monorepo root** (so cargo picks up the workspace):

```bash
# Device (arm64) + simulator (arm64 + x86_64)
cargo build -p jessica-ffi --release --target aarch64-apple-ios
cargo build -p jessica-ffi --release --target aarch64-apple-ios-sim
cargo build -p jessica-ffi --release --target x86_64-apple-ios

# Fat library for the simulator slice
lipo -create \
    target/aarch64-apple-ios-sim/release/libjessica_ffi.a \
    target/x86_64-apple-ios/release/libjessica_ffi.a \
    -output target/universal-ios-sim/libjessica_ffi.a

# cbindgen regenerates the header (already in domains/jessica-ffi/include/)
cargo run -p jessica-ffi --bin cbindgen 2>/dev/null || \
    cbindgen --config domains/jessica-ffi/cbindgen.toml \
             domains/jessica-ffi \
             --output domains/jessica-ffi/include/jessica_ffi.h

# Wrap into an xcframework
xcodebuild -create-xcframework \
    -library target/aarch64-apple-ios/release/libjessica_ffi.a \
        -headers domains/jessica-ffi/include \
    -library target/universal-ios-sim/libjessica_ffi.a \
        -headers domains/jessica-ffi/include \
    -output ios/JessicaCore/JessicaFFI.xcframework
```

Then update `JessicaCore/Package.swift` to declare a
`binaryTarget(name: "JessicaFFI", path: "JessicaFFI.xcframework")` and
add it as a `JessicaCore` dependency.

## Signing

Out of scope for M0.

M1+:

- Apple Developer account (paid).
- Manual TestFlight build on the maintainer's mac for the first
  internal alpha.
- Fastlane / `xcodebuild -allowProvisioningUpdates` once the cert
  story is stable.

## CI

Per-PR build runs on the monorepo's GitHub Actions config (see
`/Makefile` and the `make build-ios` umbrella target). Requires a
macOS runner — either GitHub-hosted (`macos-14`) or self-hosted.
Linux runners can lint the Swift sources and run `cargo test` for the
Rust crates that `JessicaCore` will wrap, but cannot `xcodebuild`.
