# 09 — Mobile platform decision

The mobile twin needs to give the primary Polish-speaking user a
voice-first assistant on the phone with the strongest possible
**on-device** ASR + LLM + TTS for **Polish**, the smallest privacy
footprint, and access to per-user voice learning.

This doc records the decision and why.

> **Decision (2026-06-12, supersedes 2026-06-11):** **Fully native
> per platform** — Swift + SwiftUI for iOS, Kotlin + Jetpack Compose
> for Android. **Business logic shared as a Rust core**
> (`crates/jessica-core/`) exposed via a clean C ABI (Swift
> Package on iOS, AAR via JNI on Android). The mobile work moves
> from `rachel/` (Flutter) to two new projects:
> - `/Users/beret/dev/jessica-ios/` (SwiftUI + Xcode)
> - `/Users/beret/dev/jessica-android/` (Compose + Gradle)
>
> `rachel/` becomes the **reference implementation of the contract**
> — the Flutter Dart codebase stays around as a sanity check that
> the cross-platform spec is implementable, but it is **not the
> shipping product**.

## 0. Why we moved off Flutter (2026-06-12)

After scaffolding `rachel/` fully on Flutter (M1 mobile work, 18 Dart
tests green, full plugin stubs on both platforms), it became clear
that the features that **define** Jessica on iOS in late 2026 are
all Swift-only:

| iOS feature             | Native access                          | Flutter access                                        |
|-------------------------|----------------------------------------|--------------------------------------------------------|
| **Foundation Models** (Apple Intelligence in-process 3B LLM, iOS 26+) | Direct Swift API with streaming tokens + structured output | Wrap via MethodChannel — per-token round-trip kills streaming UX; no structured-output sugar |
| **App Intents** (Siri/Shortcuts) | Day-1 in Swift                  | Plugins trail Apple 6-12 months per new category       |
| **Live Activities + Dynamic Island** ("Jess listening…") | ActivityKit native | Pluginy basic, brak polish |
| **Personal Voice** (clone user's voice for TTS, iOS 17+) | Speech framework | Plugin wrap available; loses fine-grained control |
| **BackgroundAssets** (download models on demand) | Swift-only | None |
| **Visual Intelligence** ("read this for me" via camera) | Swift-only | None |
| **SwiftUI new APIs** (Liquid Glass, …) | Day-1 | Flutter renders its own canvas — no access |

Android side is the same shape — Gemini Nano via AICore is Kotlin-
first, Compose Live Updates are Kotlin-only.

For a product where **the ML experience is the product** (not just
the UI), Flutter's "ship UI on both platforms" advantage doesn't
outweigh quarters of lag on the actual feature surface.

## 0a. Shared Rust core

Native UI per platform, but business logic stays shared. (These shared crates
relocate from `crates/` into the repo-root `domains/<domain>/` library tree in
Phase 3 — see [`../19-DOMAIN-ARCHITECTURE.md`](../19-DOMAIN-ARCHITECTURE.md);
names and the FFI contract are unchanged.)

```
crates/                                                  # in blazen_os repo
  blazend-ipc           (existing)
  blazend-fabric        (existing — used by Pi appliance + mobile)
  jessica-core   (NEW)
    • intent router (port from rachel/lib/intents/router.dart)
    • orchestrator state machine
    • adapter contracts (Gemini, IMAP, FB, podcasts)
    • sync log + voice ID embedding (reuse blazend-fabric)
    • SQLite-backed notes/reminders/profile stores
    • Pure Rust; no Swift, no Kotlin, no Foreign Function Interface
  jessica-ffi           (NEW)
    • cbindgen → C header → Swift Package (iOS)
    • jni-rs → JNI bindings → AAR (Android)
```

This is the **same** Rust workspace that already powers
`blazend-fabric` on the Pi. Mobile inherits the sync log + peer
types for free, with byte-for-byte compatibility on the wire.

Both `jessica-ios` and `jessica-android` link this static library
and call into it for everything that isn't UI or platform-specific
ML. UI calls native; ML calls native; everything else calls Rust.

## 0b. What survives from the Flutter work

The Flutter scaffold (`rachel/`) was **80% specification, 20% UI**.
The 80% lives on:

- All `docs/platform-mobile/` docs (architecture, build-and-ship,
  on-device ML, permissions, background modes, native plugins,
  testing, sync protocol).
- The native plugin stubs (`ios/Runner/Plugins/*.swift`,
  `android/.../plugins/*.kt`) — they were already Swift / Kotlin;
  they just stop being Flutter-managed and become the real
  implementation entry points.
- The intent router Dart code becomes the **reference** the Rust
  port is tested against (same shared YAML; identical match results).
- The fabric sync log Dart code — same, tests round-trip the Rust
  CRDT.
- All shared `docs/product/` content (this file included) — never
  was Flutter-specific.

What gets reimplemented (the 20%):
- `lib/main.dart` → `JessicaApp.swift` + `MainActivity.kt`
- `lib/ui/onboarding/onboarding_screen.dart` → SwiftUI 4-step view +
  Compose equivalent
- `lib/ui/shell/{home,briefing,settings}_tab.dart` → SwiftUI
  TabView + Compose `NavigationBar`
- `lib/ui/pairing/pairing_screen.dart` → SwiftUI + Compose

## 1. Framework comparison (re-scored 2026-06-12)

| Option                          | iOS 27 day-1 LLM | App Intents | Live Activities | Foundation Models | Cost   | Verdict |
|---------------------------------|:----------------:|:-----------:|:---------------:|:------------------:|:------:|---------|
| **Native Swift + Kotlin + shared Rust core** | ✓ | ✓ | ✓ | ✓ direct | High UI | **Picked (2026-06-12)** |
| Native Swift + Kotlin (no shared core) | ✓ | ✓ | ✓ | ✓ direct | Higher (more duplication) | Rejected — no shared logic |
| ~~Flutter + plugins~~           | ✗ (quarters lag) | partial | partial | wrap only — kills streaming | Lower | **Rejected after M1 mobile scaffold** |
| Kotlin Multiplatform + SwiftUI/Compose | ✓ via JNI/Kotlin/Native | ✓ | ✓ | direct via Swift | Medium | Considered — Kotlin/Native on iOS adds a runtime layer + KMP iOS interop has rough edges for our audio path |
| React Native (new arch.)        | ✗                | partial    | partial         | wrap only         | Lower  | Rejected — even weaker ML ecosystem than Flutter |
| Tauri Mobile / .NET MAUI        | ✗                | ✗          | ✗               | ✗                  | n/a    | Rejected — audio APIs immature |

### Why native + shared Rust wins

1. **Day-1 access to Foundation Models, App Intents, Live Activities,
   Personal Voice, BackgroundAssets, Visual Intelligence** — every
   iOS feature that defines the Jessica UX in late 2026.
2. **Day-1 access to Gemini Nano AICore, Compose Live Updates** on
   Android.
3. **Shared Rust core** keeps business logic (intent router, sync
   log, adapter contracts, voice profile DB) **literally identical**
   to the Pi appliance — same crates, same tests.
4. **No FFI boundary inside a feature.** UI calls native APIs
   directly; Rust handles cross-platform logic. The seam is
   between *layers*, not inside one.
5. **Plugin stubs already exist.** The Flutter mobile work produced
   `WakeWordPlugin.swift`/.kt etc. — those are exactly the native
   entry points we keep, minus the MethodChannel wrapper.
6. **Smaller binaries.** No Flutter engine (~7-10 MB), no Dart VM.
   Just our Swift/Kotlin code + a small static Rust lib.

### What we give up

- A single UI codebase. Onboarding + shell + pairing get
  reimplemented in SwiftUI and Compose (~2-3 days; design + Dart
  reference already exist).
- Flutter's hot reload. SwiftUI Previews + Compose Previews fill
  ~80% of that gap.
- The Dart ML plugin ecosystem. We don't need it — we go straight
  to Apple Speech / Google Speech / Foundation Models / Gemini Nano
  from native code.

We accept this trade.

## 2. Per-platform ML stack

### iOS (primary target)

| Layer        | Tech                                      | Notes |
|--------------|-------------------------------------------|-------|
| Wake word    | openWakeWord ONNX via CoreML conversion    | Runs on Neural Engine. ~10 ms per 80 ms window on A17/A18. |
| Wake retrain | CoreML on-device personalisation           | Per-user threshold tuning. |
| ASR          | **`Speech` framework, on-device**          | iOS 13+ has on-device Polish recognition. iOS 17+ adds streaming + custom vocab. |
| ASR backup   | `whisper.cpp` via CoreML (`small`, `medium`)| Used when offline or non-supported language; PL on `medium` works well. |
| Speaker ID   | `SoundAnalysis` + custom `SNClassifier`    | 256-d embedding; Neural Engine accelerated. |
| LLM (short)  | **Apple Intelligence Foundation Models**   | iOS 18.4+ on A17 Pro / A18 / A18 Pro. On-device ~3B model. |
| LLM (long)   | Gemini Pro (cloud, opt-in)                 | When the on-device model isn't enough. |
| TTS          | `AVSpeechSynthesizer` with `pl-PL` voice   | Polish Enhanced voice (premium download) for natural prosody. |
| Vector store | CoreML `MLFeatureProvider` + SQLite        | Voice ID + memory embeddings. |

### Android (secondary target)

| Layer        | Tech                                       | Notes |
|--------------|--------------------------------------------|-------|
| Wake word    | openWakeWord ONNX via TFLite               | NNAPI / LiteRT; on Tensor / Snapdragon SoCs. |
| Wake retrain | TFLite on-device personalisation           | Personal voice profile. |
| ASR          | **Google Speech-to-Text on-device**        | PL on Pixel 9 Pro is excellent. On lower-end Android: limited. |
| ASR backup   | `whisper.cpp` via TFLite                   | Same as iOS. |
| Speaker ID   | `mediapipe-tasks-audio` (TFLite)           | 256-d embedding. |
| LLM (short)  | **Gemini Nano** via AICore                 | Pixel 8+ / Samsung S24+. |
| LLM (long)   | Gemini Pro (cloud, opt-in)                 | Same as iOS. |
| TTS          | Android `TextToSpeech` with `pl-PL` voice  | Google Speech Service voice (premium downloadable). |
| Vector store | TFLite + SQLite                            | Same shape as iOS. |

## 3. Architecture mapping

```
                       ┌─────────────────────────────┐
                       │       Flutter UI            │
                       │  (intent surface, briefing  │
                       │   view, settings, voice     │
                       │   onboarding wizard)        │
                       └──────────────┬──────────────┘
                                      │ Dart
                       ┌──────────────▼──────────────┐
                       │  Dart business logic        │
                       │  • Orchestrator             │
                       │  • Intent router            │
                       │  • State (Hive/SQLite)      │
                       │  • Adapter contracts        │
                       └──────────────┬──────────────┘
                                      │ platform channels + Dart FFI
                  ┌───────────────────┼────────────────────┐
                  │                   │                    │
       ┌──────────▼──────────┐  ┌─────▼─────────┐  ┌───────▼────────┐
       │  iOS native (Swift) │  │ Android (Kt.) │  │ Cloud adapters │
       │  • Speech           │  │ • SpeechRecog.│  │ (Dart HTTP)    │
       │  • AVSpeechSynth    │  │ • TFLite/NNAPI│  │ • Gemini       │
       │  • SoundAnalysis    │  │ • AICore /    │  │ • Email IMAP   │
       │  • Apple Intel.     │  │   Gemini Nano │  │ • Facebook     │
       │  • CoreML           │  │ • TextToSpeech│  │ • News         │
       │  • AVAudioEngine    │  │ • Exoplayer   │  │                │
       └─────────────────────┘  └───────────────┘  └────────────────┘
```

Each native plugin exposes a slim, named interface (e.g.,
`asr_native`, `wake_native`, `tts_native`, `speaker_id_native`) so
the Dart layer is the only consumer and can swap implementations
between iOS/Android transparently.

## 4. Minimum supported OS versions

| OS                | Min version       | Reason                                  |
|-------------------|--------------------|------------------------------------------|
| iOS               | **17.0**           | Custom-vocab on-device Speech; AVAudioEngine streaming; iOS 18.4 recommended for Apple Intelligence. |
| iPadOS            | **17.0**           | Same.                                    |
| Android           | **14.0** (API 34)  | Modern foreground service rules; predictable bg-task behaviour; SpeechRecognizer on-device APIs. |

We don't bother with older OS — voice + ML is fundamentally a "new
hardware" feature.

## 5. Why iOS is the primary target

For a Polish-only primary user:

1. **Apple's Speech framework has had Polish on-device since iOS 13**;
   it's a known-mature path with a stable API.
2. **Apple Intelligence on iOS 18.4+** gives us an on-device LLM
   without a separate Gemini Nano licensing path.
3. **Personal Voice / Live Speech (iOS 17+)** is a first-class
   per-user voice learning capability already in the OS.
4. **Privacy story** is the cleanest — Mail and Calendar APIs are
   native, Keychain is mature, App Tracking Transparency is enforced.
5. **AirPods routing** (US-27) is trivial with native APIs.
6. **TTS Polish voices** on iOS are some of the best on any consumer
   platform.

Android is supported for users who prefer it, but the primary user
profile drives the priorities.

## 6. Cloud-side: API choice

For all cloud-dependent features (Gemini, email, Facebook, news), we
use the **same HTTP clients** as `blazen_os` — written in Dart this
time but speaking the same upstream protocols. The adapter shapes are
documented in [`05-INTEGRATIONS.md`](05-INTEGRATIONS.md).

Cross-language type sharing (Pi Rust/Python ↔ Phone Dart) is done by
keeping JSON Schemas in `docs/_schema/` and generating types in both
toolchains. The `blazen_os` repo already has this set up
(`configs/_schema/events/`); we extend it for the
integration messages.

## 7. Build + ship

| Aspect          | Choice                                         |
|-----------------|------------------------------------------------|
| Build system    | Flutter SDK pinned in `flutter-version.txt`.   |
| iOS pipeline    | Fastlane via GH Actions (signed locally for TestFlight, automated for production). |
| Android pipeline| Gradle + AAB; Play Console for distribution.   |
| Testing         | `flutter test` (unit), `integration_test` (UI), `tflite_flutter_helper` round-trip tests, simulator + emulator + real-device matrix. |
| Crash reporting | Opt-in only. No defaults. (See `08-PRIVACY-AND-CLOUD.md`.) |
| Code coverage   | `flutter test --coverage` + Codecov. |

CI matches the `blazen_os` shape (Linux runner — Pixel emulator on
GitHub-hosted, iOS sim on a self-hosted macOS runner or `paul`'s
Asahi VM if we go that far).

## 8. What we'll need from the maintainer

To ship the first build:

- **Apple Developer account** ($99/year) for signing + TestFlight.
- **Google Play Console** account ($25 one-time).
- **A reference iPhone 15 Pro+** for on-device dev (simulator can't
  test Apple Intelligence or Speech entitlements properly).
- **A reference Pixel 9 Pro** for on-device Android dev.
- **Google Cloud Project** with Gemini API enabled.
- **Facebook App** with developer mode enabled + the relevant scopes
  requested (long approval cycle — start early).

Estimated lead time before first internal alpha: ~6 weeks of focused
work (≈ M2-M3 in the `rachel` roadmap).

## 9. PL TL;DR

Aplikacja mobilna `rachel` powstaje we **Flutterze** (jeden kod dla
iOS i Androida) z natywnymi pluginami ML na każdej platformie.
**Główny target to iOS** — bo Apple ma najlepsze polskie ASR
on-device, Apple Intelligence daje lokalny model językowy od iOS
18.4, Personal Voice umożliwia uczenie się głosu użytkownika, a
prywatność jest pierwszej klasy. Android jest wspierany, ale jako
drugi priorytet. Konkretne telefony i akcesoria — patrz
[`10-MOBILE-HARDWARE.md`](10-MOBILE-HARDWARE.md).
