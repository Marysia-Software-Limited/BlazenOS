import Foundation

/// FFI seam — the only place that ever calls into Rust.
///
/// M0 stub: every method returns `nil` or `0`. The body is filled in
/// during M1 once `JessicaFFI.xcframework` is built from
/// `crates/jessica-ffi`. The function names and signatures match the
/// C ABI exported by `jessica-ffi/src/lib.rs` and consumed by Swift
/// via cbindgen-generated headers.
///
/// Keep this seam thin: every public Swift type lives elsewhere.
enum JessicaFFI {

    /// `jessica_ffi_new` — returns an opaque handle (M1).
    static func makeHandle() -> OpaquePointer? { nil }

    /// `jessica_ffi_free` — releases a handle (M1).
    static func free(_ handle: OpaquePointer?) {}

    /// `jessica_ffi_load_intents` — returns one of the JESSICA_OK / ERR codes.
    static func loadIntents(_ handle: OpaquePointer?, yaml: String) -> Int32 {
        JESSICA_ERR_BAD_HANDLE
    }

    /// `jessica_ffi_match_intent` — returns the FFI's JSON output or nil.
    static func matchIntent(
        _ handle: OpaquePointer?,
        transcript: String,
        language: String
    ) -> String? {
        nil
    }

    /// `jessica_ffi_intent_count`.
    static func intentCount(_ handle: OpaquePointer?) -> Int64 { 0 }
}

let JESSICA_OK: Int32 = 0
let JESSICA_ERR_BAD_HANDLE: Int32 = -1
let JESSICA_ERR_BAD_UTF8: Int32 = -2
let JESSICA_ERR_BAD_INPUT: Int32 = -3
let JESSICA_ERR_PANIC: Int32 = -99
