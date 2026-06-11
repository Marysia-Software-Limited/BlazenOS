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

## M0 vs M1

| Concern              | M0 (now)                                | M1 (target)                                      |
|----------------------|-----------------------------------------|--------------------------------------------------|
| `JessicaCore` body   | Pure Swift fallback (`PureSwiftIntents`) | FFI calls into `JessicaFFI.xcframework`         |
| FFI library          | not built                               | `cargo build -p jessica-ffi --release` → cbindgen → `xcodebuild -create-xcframework` |
| FFI seam             | `JessicaFFI.swift` returns nil / 0      | calls `jessica_ffi_*` C symbols                  |
| Voice loop           | UI placeholder                          | `AVAudioEngine` + `Speech` framework + `AVSpeechSynthesizer` |
| LLM (short)          | n/a                                     | Apple Intelligence Foundation Models (iOS 18.4+) |

The Swift public API (`JessicaCore.init`, `.loadIntents`,
`.matchIntent`, `.intentCount`) stays identical across M0 and M1 — only
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
