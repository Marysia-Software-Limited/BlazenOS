# Jessica — Android

Native Android implementation of the **Jessica** voice-first assistant.
Lives inside the `blazen_os` monorepo at `android/`, side-by-side with the
iOS app (`ios/`), the Pi 5 appliance (`rpi5/`), and the shared Rust core
(`domains/jessica-core`, `domains/jessica-ffi`).

| Item              | Choice                                       |
|-------------------|----------------------------------------------|
| Language          | Kotlin 2.0                                   |
| UI                | Jetpack Compose (Material 3)                 |
| Build             | Gradle 8.10 / AGP 8.7                        |
| Min SDK           | **30** (Android 11) — modern foreground service rules, predictable bg tasks |
| Target / Compile  | **35** (Android 15) — Compose 1.7 baseline; flip to 36 for AICore Gemini Nano |
| Languages (PL+EN) | Polish is the development default (`pl`); English ships day-1 |
| ML stack          | openWakeWord (TFLite/NNAPI) + Google on-device Speech + Gemini Nano (AICore) + Android `TextToSpeech` |
| Shared core       | `domains/jessica-core/` (Rust) via `libjessica_ffi.so` (JNI) |

## Repo layout

```
blazen_os/android/
├── app/                  # Compose UI + ML wiring (the shippable APK)
│   └── src/main/
│       ├── kotlin/os/blazen/jessica/   # MainActivity, JessicaApp, UI
│       ├── res/                        # strings (PL/EN), themes
│       └── AndroidManifest.xml
├── core/                 # Kotlin port + JNI bindings to jessica-ffi
│   └── src/main/kotlin/os/blazen/jessica/core/
│       ├── JessicaCore.kt              # Idiomatic Kotlin API
│       ├── JessicaCoreNative.kt        # external fun bindings (M1)
│       └── IntentMatch.kt              # Shared data classes
├── docs/                 # Per-project docs (architecture, build, ML stack)
├── build.gradle.kts      # Root build (plugin versions only)
├── settings.gradle.kts   # :app, :core
├── gradle.properties
├── local.properties.example
├── Makefile              # Convenience targets
├── AGENTS.md
├── CLAUDE.md
└── README.md             # (this file)
```

## How it relates to the rest of the monorepo

| Concern                              | Lives in                                       |
|--------------------------------------|------------------------------------------------|
| Intent router, sync log (CRDT)       | `domains/jessica-core/`                  |
| C ABI + JNI bridge                   | `domains/jessica-ffi/`                          |
| Shared intent YAML catalogue         | `configs/intents/`                             |
| Product spec (PL+EN, shared with iOS)| `docs/product/`                                |
| iOS twin                             | `ios/`                                         |
| Pi 5 appliance                       | `rpi5/src/blazend/`, `rpi5/crates/blazend-*`, `scripts/` |

The Rust workspace lives at `crates/Cargo.toml` (one workspace root for the
whole repo). The Android `:core` module currently ships a **hand-written
Kotlin port** of the public API so the UI compiles end-to-end in M0;
during M1 the bodies are swapped for `external fun` JNI declarations
against `libjessica_ffi.so` and the Rust crate becomes the source of truth.

## Quick start

```bash
cd android/
make local-properties        # writes local.properties from your $ANDROID_HOME
make wrapper                 # one-time: install the Gradle wrapper
make build                   # ./gradlew assembleDebug
make test                    # ./gradlew :core:test
make install                 # adb install -r app-debug.apk on a connected device
```

See [`docs/build.md`](docs/build.md) for the full build matrix (host
toolchain, NDK, cargo-ndk, signing).

## Status

**M0:** scaffold builds; Compose UI displays a placeholder Polish/English
home screen; `:core` ships a pure-Kotlin reference implementation that
returns the same intent matches as the Rust crate.

**M1 (now):** tap-to-talk voice loop end-to-end. `JessicaOrchestrator`
drives `SpeechRecognizer` → `JessicaCore.matchIntent` → `ReplyGenerator`
→ `TextToSpeech` through an `Idle/Listening/Thinking/Speaking/Error`
state machine. `PermissionGate` handles the `RECORD_AUDIO` runtime
prompt. Language toggle (PL / EN / Auto) on the home screen; voice
intents `language_pin_pl/en` and `language_unpin` also flip it.
Foreground-service shell (`JessicaForegroundService`) declared and
notification-channel-ready for the M2 always-listen loop.

**M2 (next):** wake word — openWakeWord ONNX via TFLite/NNAPI driving
the foreground service; Gemini Nano via AICore for the open-domain
reply path; pairing flow with the Pi appliance over the fabric sync
log; flip `:core` to JNI against `libjessica_ffi.so`.

See [`docs/15-DEV-WORKFLOW.md`](../docs/15-DEV-WORKFLOW.md) for how this
project fits into the monorepo workflow on paul.
