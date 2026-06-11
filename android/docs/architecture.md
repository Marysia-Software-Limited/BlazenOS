# Android architecture

```
┌────────────────────────────────────────────────────┐
│  app/  (Compose UI, ML wiring, Activity, manifest)  │
│   ├── MainActivity                                  │
│   ├── ui/ (HomeScreen, theme, future shell)         │
│   └── voice/ (wake/asr/tts plugins — M1)            │
└──────────────────────┬─────────────────────────────┘
                       │  Kotlin call
┌──────────────────────▼─────────────────────────────┐
│  core/  (Kotlin façade — JessicaCore)               │
│   ├── JessicaCore        (idiomatic API)            │
│   ├── JessicaCoreNative  (internal external fun)    │
│   └── IntentMatch        (data class)               │
└──────────────────────┬─────────────────────────────┘
                       │  JNI                M1 only
┌──────────────────────▼─────────────────────────────┐
│  crates/jessica-ffi  (Rust → cdylib)                │
│   └── libjessica_ffi.so   (jniLibs/<abi>/)          │
└──────────────────────┬─────────────────────────────┘
                       │  Rust call
┌──────────────────────▼─────────────────────────────┐
│  crates/jessica-core                         │
│   ├── intent.rs (regex router)                      │
│   ├── lib.rs    (SyncLog, Fact, IntentRouter)       │
│   └── (shared with iOS — single source of truth)    │
└────────────────────────────────────────────────────┘
```

## Why two modules

- **`:app`** is the only module that depends on AGP / AndroidX /
  Compose. It's the shippable APK and the natural place for OS-touching
  code (mic, TTS, AICore, notifications).
- **`:core`** is plain Kotlin JVM. It compiles on a laptop without the
  Android SDK, which keeps the round-trip for unit-test work fast and
  lets the iOS team check the contract by reading the Kotlin source.

## M0 vs M1

| Concern              | M0 (now)                                | M1 (target)                                   |
|----------------------|-----------------------------------------|-----------------------------------------------|
| `:core` body         | Pure Kotlin port (`PureKotlinIntents`)  | JNI delegate to `libjessica_ffi.so`           |
| Native lib           | not built                               | `cargo ndk -t arm64-v8a -t x86_64 build`      |
| FFI consumer         | none (Kotlin runs)                      | `System.loadLibrary("jessica_ffi")` on first use |
| JSON parsing of FFI  | n/a                                     | kotlinx.serialization (new `:core` dep)       |
| Voice loop           | UI placeholder                          | foreground service + `SpeechRecognizer` + TTS |

The Kotlin public API (`JessicaCore.create`, `.loadIntents`,
`.matchIntent`, `.intentCount`, `.close`) stays identical across M0 and
M1 — only the implementation switches.

## Naming choices

- **Package:** `os.blazen.jessica.*` — the JNI exports in
  `crates/jessica-ffi/src/jni_bridge.rs` already bake in
  `Java_os_blazen_jessica_core_*`, so this naming is the contract.
- **Native lib:** `jessica_ffi` (loaded as `libjessica_ffi.so`).
- **Application ID:** `os.blazen.jessica` — same as the package for
  simplicity; we'll split if the appstore presence ever needs it.

## Threading

- All `JessicaCore` calls are safe to invoke from any thread (the Rust
  side guards the inner state with a `Mutex`).
- Compose stays on the main thread; long-running calls (wake loop,
  ASR, LLM) belong in a foreground service.
- TODO M1: define the foreground service's lifecycle relative to
  Activity death (the spec lives in `docs/product/`).
