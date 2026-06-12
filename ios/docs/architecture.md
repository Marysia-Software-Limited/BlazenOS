# iOS architecture

```
┌────────────────────────────────────────────────────┐
│  Jessica/  (SwiftUI app target — Info.plist target)│
│   ├── App/JessicaApp.swift  (@main)                 │
│   ├── Views/HomeView, Pairing, Shell, Onboarding    │
│   ├── Voice/  (wake/asr/tts plugins — M1)           │
│   ├── Fabric/ (FabricClient — sync log seam — M1)   │
│   ├── Storage/ (Keychain / SQLite seam — M1)        │
│   └── Resources/                                    │
└──────────────────────┬─────────────────────────────┘
                       │  Swift call
┌──────────────────────▼─────────────────────────────┐
│  JessicaCore/  (Swift Package)                      │
│   ├── JessicaCore          (idiomatic API)          │
│   ├── JessicaFFI           (internal C ABI seam)    │
│   ├── IntentMatch          (Sendable + Codable)     │
│   └── PureSwiftIntents     (M0 fallback)            │
└──────────────────────┬─────────────────────────────┘
                       │  C ABI                M1 only
┌──────────────────────▼─────────────────────────────┐
│  crates/jessica-ffi  (Rust → staticlib + xcframework)│
│   └── JessicaFFI.xcframework (cbindgen → C header)  │
└──────────────────────┬─────────────────────────────┘
                       │  Rust call
┌──────────────────────▼─────────────────────────────┐
│  crates/jessica-core                         │
│   ├── intent.rs (regex router)                      │
│   ├── lib.rs    (SyncLog, Fact, IntentRouter)       │
│   └── (shared with Android — single source of truth)│
└────────────────────────────────────────────────────┘
```

## Why two targets

- **`Jessica`** is the app target — it owns the Info.plist, the
  Assets.xcassets, the bundle resources, and everything that talks to
  Apple OS frameworks (`Speech`, `AVFoundation`, Foundation Models,
  `BackgroundAssets`, App Intents).
- **`JessicaCore`** is a Swift Package, no Apple-framework deps. It
  builds with `swift test` on a plain mac (no Xcode UI), which keeps
  the unit-test loop fast and lets the Android team verify the
  cross-platform contract by reading Swift source.

## Voice loop (M1)

```
   user taps Start            Idle ──tap──▶ Listening (wake window open)
                                            │
   WakeWordDetector            continuous SpeechAnalyzer scanning partials
   yields .triggered           for "jessica" / "jessico" / "dżesika" …
   ───────────────────────────▶┤
                               │ stop wake (free mic)
                               ▼
                          Recognizing (SpeechEngine single-utterance ASR)
                               │ ASR returns transcript
                               ▼
                          stripWakePhrase + JessicaCore.matchIntent
                               │
       ┌─ language_*           │
       │  flip pin             │
       ├─ remember/recall ─────┼──▶ MemoryStore (JSON in Application Support)
       │                       │
       ├─ set/list/cancel ─────┼──▶ ReminderScheduler (UNCalendarNotificationTrigger)
       │  reminder             │
       │                       │
       ├─ news_query     ──────┼──▶ GeminiClient (gemini-1.5-flash:generateContent)
       │                       │
       ├─ canned intent  ──────┼──▶ ReplyGenerator
       │                       │
       └─ no match       ──────┼──▶ FoundationModelResponder
                               │      └─ fallback ─▶ GeminiClient
                               │              └─ fallback ─▶ canned "I don't know"
                               ▼
                          Speaking (AVSpeechSynthesizer)
                               │ TTS finishes
                               ▼
                          start wake again (back to Listening)
```

`Stop` button cancels the whole pipeline (`pipelineTask.cancel()` +
`wake.stop()` + `tts.stopSpeaking()`). Tap during `Speaking` interrupts
TTS only; tap during any other non-idle state calls `stop()`. The
`VoicePipeline` is owned by `CoreHost` so its state survives view
restarts (rotation, scene re-entry) — mirrors Android's
`JessicaApp.orchestrator` ownership.

## Milestones

| Concern              | M0                                       | M1 (now)                                                     | M2 (next)                                          |
|----------------------|------------------------------------------|--------------------------------------------------------------|----------------------------------------------------|
| `JessicaCore` body   | Pure Swift fallback (`PureSwiftIntents`) | Same (PureSwiftIntents drives the M1 UI)                     | FFI calls into `JessicaFFI.xcframework`            |
| FFI library          | not built                                | not built                                                    | `cargo build -p jessica-ffi --release` + cbindgen + `xcodebuild -create-xcframework` |
| FFI seam             | `JessicaFFI.swift` returns nil / 0       | unchanged                                                    | calls `jessica_ffi_*` C symbols                    |
| Voice loop           | UI placeholder                           | tap-to-start always-listen with continuous wake-phrase scan + utterance ASR + intent routing | openWakeWord CoreML on Neural Engine; in-Place app-intents wiring |
| Wake word            | not present                              | continuous `SpeechAnalyzer` scanning partials for "jessica"/"jessico" tokens (debounced 4 s) | openWakeWord ONNX → CoreML on Neural Engine        |
| Permissions          | n/a                                      | mic gate (`PermissionDeniedView` + Settings link); `UNUserNotifications` prompt on first reminder | `NSPersonalVoiceUsageDescription` runtime prompt   |
| Language pinning     | n/a                                      | UI toggle + voice intents (`language_pin_pl/en`, `language_unpin`) | per-utterance auto-detect                     |
| Memory               | n/a                                      | `MemoryStore` — JSON file in Application Support; remember/recall/forget facts | SQLite + per-fact vector embedding for fuzzy recall |
| Reminders            | n/a                                      | `ReminderScheduler` — `UNCalendarNotificationTrigger`; `NSDataDetector` + PL relative-time fallback | richer NL date parsing; recurring reminders        |
| Cloud (Gemini)       | n/a                                      | `GeminiClient` — `gemini-1.5-flash:generateContent`; API key in Keychain; opt-in via Settings | streaming responses; tool-use for live web fetches |
| On-device LLM        | n/a                                      | `FoundationModelResponder` — `LanguageModelSession` with `@Generable IntentResult` (single-turn) | multi-turn conversational context across utterances |
| Reply path           | n/a                                      | dispatch: canned (ReplyGenerator) / Memory / Reminder / Gemini / FM → cloud fallback → unknown reply | unified planner with tool calls                    |
| Settings UI          | n/a                                      | `SettingsView` sheet — Gemini key entry, memory inspector, reminder list, clear/cancel | pairing flow with the Pi appliance                 |

The Swift public API (`JessicaCore.init`, `.loadIntents`,
`.matchIntent`, `.intentCount`) stays identical across M0 → M2 — only
the body switches.

## Concurrency

- `SWIFT_STRICT_CONCURRENCY = complete` in `project.yml`.
- `JessicaCore` is `@unchecked Sendable` — the inner `NSLock` enforces
  the same single-writer invariant the Rust `Mutex<JessicaInner>` does
  on the Pi.
- UI runs on `@MainActor`; ML and FFI calls are off-main.

## Naming choices

- **Bundle ID prefix:** `os.blazen.jessica` — same as Android, same as
  the FFI's JNI exports (`os.blazen.jessica.core.JessicaCoreNative`).
  Keeps the cross-platform contract grep-able.
- **Swift Package name:** `JessicaCore` (matches the iOS twin docs in
  the Rust crate's lib.rs doc comment).
- **Future xcframework name:** `JessicaFFI.xcframework`.
