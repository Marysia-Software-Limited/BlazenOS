# Build & toolchain

## Prerequisites

| Tool             | Version              | Install                                              |
|------------------|----------------------|------------------------------------------------------|
| JDK              | 17                   | `brew install openjdk@17` (or system package manager)|
| Android SDK      | 35                   | Android Studio → SDK Manager                         |
| Android NDK (M1) | r26 / 26.3.11579264  | SDK Manager → SDK Tools → NDK (Side by side)         |
| Gradle           | 8.10.2               | `brew install gradle` OR `make wrapper`              |
| `adb`            | platform-tools 35    | bundled with SDK                                     |
| Rust + cargo-ndk (M1) | stable + cargo-ndk 3.5 | `cargo install cargo-ndk`                       |

## One-time setup

```bash
cd android/
make local-properties        # writes local.properties from $ANDROID_HOME
make wrapper                 # installs ./gradlew (skip if `gradle` is global)
```

## Build matrix

```bash
make build                   # ./gradlew assembleDebug
make test                    # :core JVM tests
make lint                    # ./gradlew lint
make install                 # adb install -r app-debug.apk on a connected device
make debug                   # installDebug + am start
make clean                   # ./gradlew clean + wipe build/ dirs
```

## M1: cross-compiling `libjessica_ffi.so`

From the **monorepo root** (so cargo picks up the workspace):

```bash
cargo install cargo-ndk
cargo ndk \
    -t arm64-v8a \
    -t x86_64 \
    -o android/app/src/main/jniLibs \
    build -p jessica-ffi --release
```

Then flip `JessicaCoreNative.LIB_AVAILABLE` to `true` in
`core/src/main/kotlin/os/blazen/jessica/core/JessicaCoreNative.kt`.

## Signing (release)

Out of scope for M0. M1+:

- Generate an upload keystore (`keytool -genkey -v -keystore ...`).
- Store the keystore outside the repo; reference via env-var-resolved
  Gradle props (`signingConfigs.release.storeFile = System.getenv("UPLOAD_KEYSTORE")`).
- Never commit `*.keystore`, `*.jks`, or `keystore.properties`.

## CI

Per-PR build runs on the monorepo's GitHub Actions config (see
`/Makefile` and the `make build-android` umbrella target). Wiring is
trivial — Ubuntu runner + `actions/setup-java@v4` + `gradle :app:build`.
The release pipeline (signing + Play Console) is paul-only and gated on
a manual workflow dispatch.
