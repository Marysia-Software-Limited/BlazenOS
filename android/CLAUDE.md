# Claude rules — `android/`

Claude-specific operational notes for the Android implementation. Reads
on top of [`AGENTS.md`](AGENTS.md) (cross-agent baseline) and the
monorepo-level [`/CLAUDE.md`](../CLAUDE.md).

When this file disagrees with `/CLAUDE.md`, prefer the **most
restrictive** guidance unless the user overrides.

## 1. Read first (in order)

1. `/CLAUDE.md` (monorepo)
2. `/AGENTS.md` (monorepo)
3. `android/CLAUDE.md` (this file)
4. `android/AGENTS.md`
5. `android/README.md`
6. `android/docs/architecture.md` → `docs/build.md` → `docs/ml-stack.md`

When in doubt about an FFI contract, also read:
- `domains/jessica-core/src/intent.rs`
- `domains/jessica-ffi/src/jni_bridge.rs`

## 2. Project snapshot

- Kotlin 2.0 / AGP 8.7 / Gradle 8.10 / minSdk 30 / target 35.
- Two modules: `:app` (Compose UI + ML wiring), `:core` (Kotlin port of
  the Rust mobile core — placeholder in M0, JNI in M1).
- Namespace: `os.blazen.jessica` (matches the JNI class path baked into
  `domains/jessica-ffi/src/jni_bridge.rs`).
- Native lib: `libjessica_ffi.so`, loaded via
  `System.loadLibrary("jessica_ffi")` once `:core` switches to JNI.

## 3. Operational notes

1. **Plan before non-trivial Compose work.** UI changes that touch the
   shell, pairing, or onboarding flows go through `EnterPlanMode` —
   they're cross-platform spec territory (the iOS twin must match).
2. **Track multi-step work with `TaskCreate`/`TaskUpdate`.**
3. **Prefer dedicated tools** (`Read`, `Edit`, `Write`, `Glob`, `Grep`).
   Use `Bash` only for `gradle`, `adb`, `make`.
4. **Confirm before:** uninstalling apps on a connected device, signing
   release builds, force-pushing branches, deleting `app/build/`.
5. **Memory hygiene.** Don't memorize Gradle file paths — derive from the
   tree. Save only genuinely surprising user preferences.

## 4. Editing conventions

1. Always `Read` a Gradle file or AndroidManifest before editing it.
2. Minimal, surgical diffs.
3. Never modify Gradle wrapper jars (`gradle/wrapper/gradle-wrapper.jar`)
   except via `./gradlew wrapper --gradle-version=...`.
4. Never weaken a failing test. Investigate root causes.
5. PL+EN parity: when you add a `getString(R.string.foo)`, add both
   `values/strings.xml` and `values-pl/strings.xml` entries together.
6. When you add a new `external fun` in `JessicaCoreNative.kt`, add the
   matching `Java_os_blazen_jessica_core_JessicaCoreNative_<name>`
   function in `domains/jessica-ffi/src/jni_bridge.rs` in the same
   commit.

## 5. Verification checklist (before declaring task done)

- [ ] `make test` is green.
- [ ] `make build` is green with no new warnings.
- [ ] Any new `R.string.*` exists in both `values/` and `values-pl/`.
- [ ] Any new `external fun` has a matching Rust JNI export.
- [ ] Docs (`android/docs/`) reflect the new behaviour.
- [ ] No `local.properties`, `*.keystore`, or `app/build/` staged.
- [ ] Voice-first sanity check (monorepo `/CLAUDE.md` §7) is satisfied.
