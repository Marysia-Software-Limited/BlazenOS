# Android architecture

```
┌────────────────────────────────────────────────────────────┐
│  app/  (Compose UI, voice loop, foreground service)         │
│   ├── MainActivity   (hosts HomeScreen behind PermissionGate)│
│   ├── JessicaApp     (Application — owns the orchestrator)  │
│   ├── ui/            (HomeScreen, PermissionGate, theme)    │
│   ├── voice/                                                 │
│   │    ├── JessicaOrchestrator (state machine + StateFlow)  │
│   │    ├── JessicaAsr   (SpeechRecognizer wrapper)          │
│   │    ├── JessicaTts   (TextToSpeech wrapper)              │
│   │    └── JessicaForegroundService (M2 wake-word host)     │
│   └── brain/                                                 │
│        └── ReplyGenerator (intent → canned PL+EN reply)     │
└──────────────────────────────┬─────────────────────────────┘
                               │  Kotlin call
┌──────────────────────────────▼─────────────────────────────┐
│  core/  (Kotlin façade — JessicaCore)                       │
│   ├── JessicaCore        (idiomatic API)                    │
│   ├── JessicaCoreNative  (internal external fun)            │
│   ├── PureKotlinIntents  (M1 fallback matcher)              │
│   └── IntentMatch        (data class)                       │
└──────────────────────────────┬─────────────────────────────┘
                               │  JNI                M2 only
┌──────────────────────────────▼─────────────────────────────┐
│  domains/jessica-ffi  (Rust → cdylib)                        │
│   └── libjessica_ffi.so   (jniLibs/<abi>/)                  │
└──────────────────────────────┬─────────────────────────────┘
                               │  Rust call
┌──────────────────────────────▼─────────────────────────────┐
│  domains/jessica-core                                        │
│   ├── intent.rs (regex router)                              │
│   ├── lib.rs    (SyncLog, Fact, IntentRouter)               │
│   └── (shared with iOS — single source of truth)            │
└────────────────────────────────────────────────────────────┘
```

## Voice loop (M1)

```
  user taps mic            Idle ──tap──▶ Listening(lang, partial)
                                         │
  SpeechRecognizer fires    ─────────────┘
                                         │ onResult(transcript, lang)
                                         ▼
                                   Thinking(transcript, lang)
                                         │
                                         │ JessicaCore.matchIntent → IntentMatch?
                                         │ (+ applyLanguageIntent if language_*)
                                         │ ReplyGenerator.reply(match, effectiveLang)
                                         ▼
                                   Speaking(reply, effectiveLang)
                                         │
                                         │ TTS onDone
                                         ▼
                                       Idle
```

Tap during `Speaking` interrupts TTS; tap during `Listening` cancels ASR.

## Why two modules

- **`:app`** is the only module that depends on AGP / AndroidX /
  Compose. It's the shippable APK and the natural place for OS-touching
  code (mic, TTS, AICore, notifications).
- **`:core`** is plain Kotlin JVM. It compiles on a laptop without the
  Android SDK, which keeps the round-trip for unit-test work fast and
  lets the iOS team check the contract by reading the Kotlin source.

## Milestones

| Concern              | M0                                       | M1 (now)                                                    | M2 (next)                                          |
|----------------------|------------------------------------------|-------------------------------------------------------------|----------------------------------------------------|
| `:core` body         | Pure Kotlin port (`PureKotlinIntents`)   | Same (PureKotlinIntents drives the M1 UI)                   | JNI delegate to `libjessica_ffi.so`                |
| Native lib           | not built                                | not built                                                   | `cargo ndk -t arm64-v8a -t x86_64 build`           |
| FFI consumer         | none                                     | none (Kotlin runs)                                          | `System.loadLibrary("jessica_ffi")` on first use   |
| JSON parsing of FFI  | n/a                                      | n/a                                                         | kotlinx.serialization (new `:core` dep)            |
| Voice loop           | UI placeholder                           | tap-to-talk: `SpeechRecognizer` → intent → `TextToSpeech`   | always-listen: openWakeWord TFLite + foreground service |
| Permissions          | n/a                                      | `RECORD_AUDIO` runtime prompt via `PermissionGate`          | `POST_NOTIFICATIONS` + `FOREGROUND_SERVICE_MICROPHONE` runtime flow |
| Language pinning     | n/a                                      | UI toggle + voice intents (`language_pin_pl/en`, `language_unpin`) | per-utterance auto-detect                     |
| Reply path           | n/a                                      | canned per-intent PL+EN replies (`ReplyGenerator`)          | Gemini Nano via AICore (Pixel 8+ / S24+) behind `SDK_INT ≥ 36` |

The Kotlin public API (`JessicaCore.create`, `.loadIntents`,
`.matchIntent`, `.intentCount`, `.close`) stays identical across M0 → M2 — only the
implementation behind it switches.

## Naming choices

- **Package:** `os.blazen.jessica.*` — the JNI exports in
  `domains/jessica-ffi/src/jni_bridge.rs` already bake in
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
