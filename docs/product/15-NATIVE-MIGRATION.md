# 15 — Native migration plan (2026-06-12)

Companion to [`09-MOBILE-PLATFORM-DECISION.md`](09-MOBILE-PLATFORM-DECISION.md).
This doc is the concrete migration plan from the Flutter scaffold
(`rachel/`) to native projects.

> **Decision (2026-06-12):** Drop Flutter as the shipping mobile
> stack. New shipping projects: `ios/` (Swift + SwiftUI),
> `android/` (Kotlin + Compose). Shared business logic moves
> into `crates/jessica-mobile-core` + `crates/jessica-ffi` in the
> blazen_os repo. `rachel/` remains as the **reference
> implementation** of the contract — used to verify the Rust port
> behaves identically on the Dart side.

> **Status update (2026-06-12):** Steps 1, 2 and 5 of this doc are
> complete.
>
> - Step 1 — repo layout shipped at `/Users/beret/dev/ios/` and
>   `/Users/beret/dev/android/`.
> - Step 2 — `crates/jessica-mobile-core` + `crates/jessica-ffi`
>   exist; cargo workspace builds clean, 3 FFI tests + 6 router
>   tests + 8 fabric tests green; cbindgen emits a clean
>   `include/jessica_ffi.h`; `scripts/build-ios-xcframework.sh` and
>   `scripts/build-android-jnilibs.sh` are wired into `make ffi`
>   on each mobile project.
> - Step 5 — both native shells ship 5 screens, JessicaCore module
>   per platform with 3 unit tests each.
>
> What's still pending:
> - Running the FFI build scripts on this host (gated on
>   `rustup target add aarch64-apple-ios aarch64-apple-ios-sim
>   aarch64-linux-android armv7-linux-androideabi
>   x86_64-linux-android` + a full Xcode install).
> - Flipping `M1_FFI_AVAILABLE` in Swift / `JessicaCoreNative.isAvailable`
>   in Kotlin once the artefacts are vendored.

## 1. New repo layout

```
/Users/beret/dev/
├── blazen_os/                       # Pi appliance (paul-primary)
│   └── crates/
│       ├── blazend-ipc              # existing
│       ├── blazend-fabric           # existing, reused by mobile
│       ├── jessica-mobile-core      # NEW
│       └── jessica-ffi              # NEW
│
├── rachel/                          # Flutter REFERENCE (read-only post-migration)
│   ├── lib/                         # source of truth for Rust port tests
│   └── REFERENCE-ONLY.md            # new marker
│
├── jessica-ios/                     # NEW — Xcode + Swift + SwiftUI
│   ├── Jessica.xcodeproj/
│   ├── Jessica/
│   │   ├── App/                     # JessicaApp.swift, scenes
│   │   ├── Views/                   # SwiftUI views (onboarding, shell, pairing)
│   │   ├── Voice/                   # WakeWord, ASR, TTS, VoiceID (real Foundation Models in M3)
│   │   ├── Fabric/                  # Bonjour + TLS client
│   │   ├── Storage/                 # Keychain + SQLite (via jessica-ffi)
│   │   └── Resources/               # Assets.xcassets, Info.plist
│   ├── JessicaCore/                 # Swift Package wrapping jessica-ffi
│   └── docs/
│
└── jessica-android/                 # NEW — Gradle + Kotlin + Compose
    ├── app/
    │   ├── build.gradle.kts
    │   └── src/main/
    │       ├── java/os/blazen/jessica/
    │       │   ├── App.kt
    │       │   ├── ui/              # Compose screens (onboarding, shell, pairing)
    │       │   ├── voice/           # WakeWord, ASR, TTS, VoiceID
    │       │   ├── fabric/          # NSD + TLS client
    │       │   └── storage/         # EncryptedSharedPreferences + Room
    │       └── AndroidManifest.xml
    ├── core/                        # AAR wrapping jessica-ffi
    └── docs/
```

Each new project keeps the `docs/product/` symlink to the shared
spec (same pattern as `rachel/`).

## 2. Shared Rust crates

### `crates/jessica-mobile-core`

Pure Rust, no platform deps. Exposes a single `JessicaCore`
opaque type with methods:

```rust
pub struct JessicaCore { /* ... */ }

impl JessicaCore {
    pub fn new(config_path: &Path) -> Result<Self>;

    // Intent routing
    pub fn match_intent(&self, transcript: &str, lang: &str) -> Option<IntentMatch>;

    // Fabric sync log
    pub fn append_fact(&self, fact: Fact) -> Result<()>;
    pub fn merge_fact(&self, fact: Fact) -> Result<SyncMergeOutcome>;
    pub fn facts_of_type(&self, kind: FactType) -> Vec<Fact>;

    // Conversation memory
    pub fn record_turn(&self, user: &str, assistant: &str, lang: &str) -> Result<()>;
    pub fn close_session(&self) -> Result<ConversationSummary>;

    // Voice ID
    pub fn save_embedding(&self, vec: &[f32], from_node: &str) -> Result<()>;
    pub fn primary_user_similarity(&self, vec: &[f32]) -> f32;

    // Notes / reminders
    pub fn add_note(&self, body: &str, lang: &str, tags: &[&str]) -> Result<NoteId>;
    pub fn list_notes_for(&self, when: &str) -> Vec<Note>;
    pub fn add_reminder(&self, body: &str, due_at_ms: i64) -> Result<ReminderId>;
    pub fn due_reminders(&self) -> Vec<Reminder>;
}
```

All state persists to SQLite via `rusqlite` (bundled), file path
provided by the platform layer.

Tests round-trip:
- The same shared intent YAML the Flutter `IntentRouter` reads — for
  every PL+EN trigger we assert the Rust and Dart implementations
  return the same `IntentMatch`.
- The same `note.created` / `reminder.created` / `voice_id.embedding`
  facts the existing `blazend-fabric` tests use — CRDT outcomes must
  match exactly.

### `crates/jessica-ffi`

Crate type `staticlib` + `cdylib`. Two output modes:

| Target              | Output                       | Built via                       |
|---------------------|------------------------------|----------------------------------|
| iOS (arm64 + arm64 sim) | `libjessica_ffi.a`       | `cargo build --target aarch64-apple-ios{,-sim}` |
| Android (arm64 + armv7 + x86_64) | `libjessica_ffi.so`  | `cargo ndk -t arm64-v8a -t armeabi-v7a build` |

C header generated by `cbindgen`:

```c
typedef struct JessicaCore JessicaCore;

JessicaCore *jessica_core_new(const char *config_path);
void jessica_core_free(JessicaCore *core);

const char *jessica_match_intent(JessicaCore *core, const char *transcript, const char *lang);
int jessica_append_fact(JessicaCore *core, const char *fact_json);
...
```

iOS imports via `JessicaCore` Swift Package (SPM) that re-exports
the C header into Swift. Android imports via JNI — the AAR ships
the `.so` + a thin `JessicaCore` Kotlin object that calls JNI.

## 3. Cross-build pipeline

Two new make targets in blazen_os:

```
make ios-core         # cargo build for aarch64-apple-ios + sim, packages as Swift Package
make android-core     # cargo ndk build for arm64-v8a + armeabi-v7a + x86_64, packages as AAR
```

Outputs land in:
- `crates/target/JessicaCore.framework/` (for the Xcode project to drag-link)
- `crates/target/jessica-core.aar` (for Gradle `implementation files(...)`)

These get checked in to `jessica-ios/JessicaCore/` and
`jessica-android/core/` respectively — sources of truth are the
Rust crates, artefacts are vendored snapshots so the iOS/Android
projects build offline.

CI rebuilds both artefacts on every blazen_os commit and PRs them
into the mobile repos.

## 4. Migration steps (this iteration)

1. **Update shared decision doc** (this doc + `09-MOBILE-PLATFORM-DECISION.md`).
2. **Create the Rust core crates** (`jessica-mobile-core`,
   `jessica-ffi`) in blazen_os. Port intent router + sync log from
   Dart. Tests round-trip.
3. **Scaffold `jessica-ios`** (Xcode SwiftUI project, bundle ID
   `os.blazen.jessica`). Wire `JessicaCore` Swift Package.
   Reimplement onboarding + shell + pairing as SwiftUI views.
4. **Scaffold `jessica-android`** (Gradle Compose project). Wire
   `JessicaCore` AAR. Reimplement same screens in Compose.
5. **Mark `rachel/` as reference** with a top-level
   `REFERENCE-ONLY.md` and an updated README. Keep all docs and
   tests; halt feature development there.
6. **Update sync protocol docs**
   (`blazen_os/docs/16-SYNC-PROTOCOL.md`) — the shared boundary
   surface grows to include `jessica-ios/` and `jessica-android/`
   (they consume `docs/product/`, `configs/intents/`,
   `configs/_schema/events/` via vendored snapshots).
7. **Update HANDOFF.md** so paul Claude knows the move.

## 5. Timeline

| Day | Work                                                   |
|-----|---------------------------------------------------------|
| 1   | docs + Rust port of intent router + sync log + tests   |
| 2   | `jessica-ffi` crate + cbindgen iOS + cargo ndk Android |
| 3   | `jessica-ios` SwiftUI shell (4 screens) + hello-world Foundation Models |
| 4   | `jessica-android` Compose shell + hello-world Gemini Nano |
| 5   | Integration tests + first TestFlight + first internal APK |

## 6. What `rachel/` retains

- All documentation under `docs/platform-mobile/` is **still
  authoritative** for cross-platform mobile concerns (build pipelines,
  permissions, background modes, native plugin contracts, testing).
  The two new projects link these docs.
- All tests stay. They become the **conformance suite** for the
  Rust port: a CI job runs `flutter test` and `cargo test` and
  cross-checks that intent matches and CRDT merges agree.
- The Dart code remains useful as a quick "smoke test in macOS
  simulator" without rebuilding native binaries.

> `rachel/` does **not** get archived. It becomes the
> *specification-by-example* — a living reference the Rust core
> and the two native apps test themselves against.

## 7. PL TL;DR

Przerzucamy się z Fluttera na pełen natyw — **Swift + SwiftUI na
iOS, Kotlin + Compose na Androidzie**, ze **wspólnym rdzeniem
w Rust** (`jessica-mobile-core` + `jessica-ffi` w workspace
blazen_os). Powód: Apple Foundation Models, App Intents, Live
Activities, Personal Voice — wszystko Swift-only; podobnie Gemini
Nano AICore tylko Kotlin. Flutter dodaje kwartały lag dla każdej
nowej funkcji. Plugin stubs które macOS Claude już napisała
(WakeWordPlugin.swift, .kt itd.) zostają — przestają być wrapem
MethodChannel i stają się prawdziwymi entry pointami. UI (4
ekrany — onboarding/home/briefing/settings/pairing) przepisujemy
~2 dni z Dart referenece. Rachel/ zostaje jako reference impl
kontraktu, nie kasujemy.
