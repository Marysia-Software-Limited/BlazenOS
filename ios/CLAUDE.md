# Claude rules — `ios/`

Claude-specific operational notes for the iOS implementation. Reads
on top of [`AGENTS.md`](AGENTS.md) (cross-agent baseline) and the
monorepo-level [`/CLAUDE.md`](../CLAUDE.md).

When this file disagrees with `/CLAUDE.md`, prefer the **most
restrictive** guidance unless the user overrides.

## 1. Read first (in order)

1. `/CLAUDE.md` (monorepo)
2. `/AGENTS.md` (monorepo)
3. `ios/CLAUDE.md` (this file)
4. `ios/AGENTS.md`
5. `ios/README.md`
6. `ios/docs/architecture.md` → `docs/build.md` → `docs/ml-stack.md`

When in doubt about an FFI contract, also read:
- `crates/jessica-core/src/intent.rs`
- `crates/jessica-ffi/src/lib.rs`
- `crates/jessica-ffi/include/jessica_ffi.h`

## 2. Project snapshot

- Swift 6.0, SwiftUI, strict concurrency.
- iOS 17.0 minimum; iOS 18.4 recommended for Apple Intelligence.
- XcodeGen drives the project (`make project` writes `Jessica.xcodeproj`
  from `project.yml`). Never hand-edit the `.xcodeproj`.
- Bundle ID prefix: `os.blazen.jessica`.
- Swift Package: `JessicaCore` (path `./JessicaCore`). M0 is pure
  Swift; M1 swaps for `binaryTarget(url: "JessicaFFI.xcframework")`.

## 3. Operational notes

1. **Plan before non-trivial SwiftUI changes.** Shell / pairing /
   onboarding are cross-platform spec territory and must mirror Android.
2. **Track multi-step work with `TaskCreate`/`TaskUpdate`.**
3. **Prefer dedicated tools** (`Read`, `Edit`, `Write`, `Glob`, `Grep`).
   Use `Bash` only for `xcodebuild`, `swift`, `xcodegen`, `make`, `cargo`.
4. **Confirm before:** uninstalling a TestFlight build from a tester,
   publishing to App Store Connect, force-pushing branches, deleting
   `DerivedData/` for someone else.
5. **paul vs Mac.** Most iOS work needs a Mac for Xcode + simulator.
   On paul (Linux), Claude can edit Swift sources, `project.yml`, docs,
   and the Rust core — but cannot run `xcodebuild`. Note the limitation
   in the task summary.

## 4. Editing conventions

1. Always `Read` `project.yml` or `Package.swift` before editing.
2. Minimal, surgical diffs.
3. Never hand-edit `Jessica.xcodeproj/` — regen via `make project`.
4. Never weaken a failing test. Investigate root causes.
5. PL+EN parity: when you add a `L10n.foo`, add both keys together.
6. When you add a new FFI function in the C ABI, update the Swift
   seam in `JessicaCore/Sources/JessicaCore/JessicaFFI.swift` AND the
   corresponding JNI export in
   `crates/jessica-ffi/src/jni_bridge.rs` so the Android twin stays
   honest.

## 5. Verification checklist (before declaring task done)

- [ ] `make test` is green.
- [ ] `make build` is green (or noted as "can't run on paul").
- [ ] Any new `L10n.foo` exists in both PL and EN.
- [ ] Any new FFI call matches `jessica_ffi.h`.
- [ ] Docs (`ios/docs/`) reflect the new behaviour.
- [ ] No `.xcodeproj/`, `DerivedData/`, or signing material staged.
- [ ] Voice-first sanity check (monorepo `/CLAUDE.md` §7) is satisfied.
