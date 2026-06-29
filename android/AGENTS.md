# Agent rules — `android/`

Cross-agent rules for the Android implementation. Applies to Claude,
Codex, Junie, and any other LLM harness operating inside `blazen_os/android/`.
Defers to the monorepo-level [`/AGENTS.md`](../AGENTS.md) and
[`/CLAUDE.md`](../CLAUDE.md) for anything not stated here.

## 1. Invariants (do not violate)

1. **Native APIs only for ML.** No Flutter, no React Native, no JS bridge.
   Speech, TTS, AICore, Compose, ActivityKit equivalents — all called from
   Kotlin directly. The Rust core handles cross-platform business logic;
   the OS handles ML and UI.
2. **PL+EN parity.** Every user-facing string lives in both
   `res/values/strings.xml` and `res/values-pl/strings.xml`. A change that
   adds only the English copy is incomplete.
3. **Shared core stays Rust.** Intent routing, sync log, adapter
   contracts — `domains/jessica-core/`. The Kotlin port in `:core`
   exists for M0 only and is a temporary stand-in. Do not extend the
   business logic there; extend the Rust crate and re-bind.
4. **JNI signatures are the contract.** External-fun signatures in
   `core/src/main/kotlin/os/blazen/jessica/core/JessicaCoreNative.kt`
   MUST match `domains/jessica-ffi/src/jni_bridge.rs` byte-for-byte. If
   you change one side, regenerate / hand-edit the other in the same
   commit.
5. **Min SDK 30, target 35.** No version drift without an explicit
   product-decision doc entry. Gemini Nano (AICore) work happens behind
   a runtime `Build.VERSION.SDK_INT >= 36` guard.
6. **Voice-first sanity check.** The user has no screen for daily use.
   Any feature is incomplete if it can't be reached, discovered, and
   recovered from by voice.

## 2. Where things go

| File you want to change       | Lives in                                            |
|-------------------------------|-----------------------------------------------------|
| App UI                        | `app/src/main/kotlin/os/blazen/jessica/ui/`         |
| Activity / Application class  | `app/src/main/kotlin/os/blazen/jessica/`            |
| App-level strings + themes    | `app/src/main/res/`                                 |
| Kotlin API surface            | `core/src/main/kotlin/os/blazen/jessica/core/`      |
| JNI external-fun signatures   | `core/.../JessicaCoreNative.kt`                     |
| Unit tests (JVM)              | `core/src/test/kotlin/...`                          |
| Instrumented tests            | `app/src/androidTest/kotlin/...` (TBD)              |
| Per-project docs              | `docs/`                                             |

Anything in `crates/`, `configs/`, `rpi5/src/blazend/`, `docs/product/`
belongs to the broader monorepo — do not edit from this project.

## 3. Maintenance loop ("let do maintenance")

1. `make test` — `:core` JVM tests + lint
2. `make build` — assembleDebug; surface every warning
3. If you touched `core/`: `cargo test -p jessica-core -p jessica-ffi`
   from the repo root to keep the JNI contract honest.
4. Update `docs/` to match any behaviour change.
5. Update `res/values{,-pl}/strings.xml` together — never one without the other.

## 4. What not to commit

- `app/build/`, `core/build/`, `.gradle/`
- `local.properties` (contains `sdk.dir`)
- `*.keystore`, `*.jks` (signing material)
- `app/release/*.apk`, `*.aab`
- Anything under the monorepo's `.gitignore` for the shared Rust core
