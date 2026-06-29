# Jessica — iOS

Native iOS implementation of the **Jessica** voice-first assistant.
Lives inside the `blazen_os` monorepo at `ios/`, side-by-side with the
Android app (`android/`), the Pi 5 appliance (`rpi5/`), and the shared
Rust core (`domains/jessica-core`, `domains/jessica-ffi`).

| Item              | Choice                                                |
|-------------------|-------------------------------------------------------|
| Language          | Swift 6.0                                             |
| UI                | SwiftUI (strict concurrency)                          |
| Build             | XcodeGen (project.yml) + xcodebuild + swift test      |
| Min iOS           | **17.0** (custom-vocab on-device Speech, AVAudioEngine streaming). iOS 18.4+ recommended for Apple Intelligence Foundation Models. |
| Languages (PL+EN) | Polish is the development language (`pl`); English ships day-1 |
| ML stack          | openWakeWord (CoreML) + Apple `Speech` framework + Foundation Models + `AVSpeechSynthesizer` |
| Shared core       | `domains/jessica-core/` (Rust) via `JessicaFFI.xcframework` (cbindgen C ABI) |

## Repo layout

```
blazen_os/ios/
├── Jessica/                  # iOS app target (SwiftUI)
│   ├── App/JessicaApp.swift  # @main entry
│   ├── Views/                # Home, Onboarding, Pairing, Shell
│   ├── Voice/                # Wake / ASR / TTS plugins (M1)
│   ├── Fabric/               # FabricClient — sync log seam
│   ├── Storage/              # Keychain / app group / SQLite
│   └── Resources/
│       ├── Info.plist
│       ├── L10n.swift        # Strongly-typed PL + EN strings
│       ├── intents-system.yaml
│       └── Assets.xcassets/
├── JessicaCore/              # Swift Package wrapping jessica-ffi
│   ├── Package.swift
│   ├── Sources/JessicaCore/  # Public Swift API + FFI seam
│   └── Tests/JessicaCoreTests/
├── JessicaTests/             # App-level integration tests
├── project.yml               # XcodeGen spec — `make project` writes Jessica.xcodeproj
├── Makefile
├── AGENTS.md
├── CLAUDE.md
└── README.md                 # (this file)
```

## How it relates to the rest of the monorepo

| Concern                              | Lives in                                       |
|--------------------------------------|------------------------------------------------|
| Intent router, sync log (CRDT)       | `domains/jessica-core/`                  |
| C ABI                                | `domains/jessica-ffi/` (cbindgen → `jessica_ffi.h`) |
| Shared intent YAML catalogue         | `configs/intents/`                             |
| Product spec (PL+EN, shared w/ Android)| `docs/product/`                              |
| Android twin                         | `android/`                                     |
| Pi 5 appliance                       | `rpi5/src/blazend/`, `rpi5/<domain>/blazend-*`, `scripts/` |

The Rust workspace lives at `crates/Cargo.toml`. The iOS `JessicaCore`
Swift package currently ships a **pure-Swift placeholder** so the app
compiles end-to-end in M0; during M1 the placeholder is replaced with a
`binaryTarget(url:)` for `JessicaFFI.xcframework` built from
`domains/jessica-ffi` via cargo + cbindgen.

## Quick start

```bash
cd ios/
brew install xcodegen        # one-time
make project                 # regen Jessica.xcodeproj from project.yml
make test                    # swift test JessicaCoreTests (no Xcode UI required)
make build                   # xcodebuild for the iPhone 16 simulator
make debug                   # open the project in Xcode
```

See [`docs/build.md`](docs/build.md) for the full build matrix
(simulator, device, signing, M1 xcframework wiring).

## Status

**M0:** scaffold builds; SwiftUI home view renders a placeholder PL/EN
greeting; `JessicaCore` ships a pure-Swift reference implementation that
returns the same intent matches as the Rust crate.

**M1 (now):** working voice agent prototype. `VoicePipeline`
(`@Observable @MainActor`) drives a `WakeWordDetector` that listens
continuously via `SpeechAnalyzer` + `SpeechTranscriber` and scans
partials for "jessica" / "jessico" (debounced 4 s) → `SpeechEngine`
captures the post-wake utterance → routing dispatches to
`MemoryStore` (remember / recall facts), `ReminderScheduler`
(`UserNotifications` calendar triggers + `NSDataDetector` date parse),
`GeminiClient` (cloud `gemini-1.5-flash:generateContent` for news /
open-domain queries, API key in Keychain), or `FoundationModelResponder`
(on-device LLM) → `TextToSpeech` (`AVSpeechSynthesizer`) reads the
reply. HomeView shows state, language toggle (PL / EN / Auto), last
turn, and a toolbar gear that opens a `SettingsView` for Gemini key /
memory inspector / reminder list. `CoreHost` owns every singleton so
state survives view restarts.

**M2 (next):** openWakeWord CoreML on the Neural Engine inside
`WakeWordDetector` for background-mode listening; Personal Voice
opt-in flow for TTS; pairing with the Pi appliance over the fabric
sync log; multi-turn `LanguageModelSession` context; richer date
parsing for reminders; flip `JessicaCore` to call the Rust FFI via
`JessicaFFI.xcframework`.

See [`docs/15-DEV-WORKFLOW.md`](../docs/15-DEV-WORKFLOW.md) for how this
project fits into the monorepo workflow on paul (note: iOS work
ultimately needs a Mac for Xcode; paul drives the Rust core, the docs,
and the cross-platform contract).
