//! C ABI + JNI bridge over [`jessica_core`].
//!
//! Two surfaces, one body:
//!
//! - **C ABI** (`extern "C"`, exported via `staticlib` + `cdylib`).
//!   Bindings on iOS go through cbindgen → `jessica_ffi.h` →
//!   Swift Package `JessicaFFI` (consumed by
//!   `/Users/beret/dev/ios/JessicaCore`).
//! - **JNI** (`#[cfg(target_os = "android")]`). Bindings on Android
//!   are hand-written `Java_os_blazen_jessica_core_*` functions that
//!   delegate to the same internal API.
//!
//! Invariants:
//! - All input strings are `(*const u8, usize)` byte buffers — they
//!   do NOT have to be NUL-terminated. UTF-8 is enforced; invalid
//!   UTF-8 returns the standard "bad input" sentinel.
//! - Returned strings are heap-allocated NUL-terminated C strings.
//!   The caller MUST hand them back via [`jessica_ffi_free_string`].
//! - All handle operations are panic-safe (panics are caught and
//!   reported as the "internal error" sentinel — never unwound across
//!   the FFI boundary).

#![deny(unsafe_op_in_unsafe_fn)]
#![allow(clippy::missing_safety_doc)]

use std::ffi::{c_char, CString};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::Mutex;

use jessica_core::{IntentRouter, SyncLog, SyncMergeOutcome};

#[cfg(target_os = "android")]
pub mod jni_bridge;

// ---------------------------------------------------------------------------
// Handle
// ---------------------------------------------------------------------------

/// Opaque handle to the per-process Jessica state. Behind a `Mutex`
/// so the iOS `MainActor` and the Android `Dispatchers.Main` can
/// both reach it safely.
pub struct JessicaHandle {
    inner: Mutex<JessicaInner>,
}

#[derive(Default)]
struct JessicaInner {
    router: Option<IntentRouter>,
    log: SyncLog,
}

impl JessicaHandle {
    fn new() -> Self {
        Self {
            inner: Mutex::new(JessicaInner::default()),
        }
    }
}

// ---------------------------------------------------------------------------
// Merge outcome enum (mirrored from blazend-fabric)
// ---------------------------------------------------------------------------

/// Status code returned by [`jessica_ffi_merge_fact`].
///
/// Non-negative values are real outcomes; negative values are errors
/// (see `JESSICA_ERR_*` constants for the encoded reasons).
#[repr(i32)]
#[derive(Debug, Copy, Clone, PartialEq, Eq)]
pub enum JessicaMergeOutcome {
    /// Fact stored fresh.
    Accepted = 0,
    /// Identical fact already present — no-op.
    DuplicateNoOp = 1,
    /// Existing fact superseded by a newer one.
    ResolvedNewer = 2,
    /// Local fact kept; incoming fact discarded.
    ResolvedKeepLocal = 3,
}

impl From<SyncMergeOutcome> for JessicaMergeOutcome {
    fn from(value: SyncMergeOutcome) -> Self {
        match value {
            SyncMergeOutcome::Accepted => Self::Accepted,
            SyncMergeOutcome::DuplicateNoOp => Self::DuplicateNoOp,
            SyncMergeOutcome::ResolvedNewer => Self::ResolvedNewer,
            SyncMergeOutcome::ResolvedKeepLocal => Self::ResolvedKeepLocal,
        }
    }
}

/// Returned by FFI ops that need to signal a binary failure (instead
/// of a real outcome). Kept as separate constants so cbindgen emits
/// them as `#define`s — easier on Swift's importer.
pub const JESSICA_OK: i32 = 0;
pub const JESSICA_ERR_BAD_HANDLE: i32 = -1;
pub const JESSICA_ERR_BAD_UTF8: i32 = -2;
pub const JESSICA_ERR_BAD_INPUT: i32 = -3;
pub const JESSICA_ERR_PANIC: i32 = -99;

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------

/// Create a new Jessica state handle.
///
/// The caller owns the returned pointer and MUST eventually call
/// [`jessica_ffi_free`] on it.
#[unsafe(no_mangle)]
pub extern "C" fn jessica_ffi_new() -> *mut JessicaHandle {
    Box::into_raw(Box::new(JessicaHandle::new()))
}

/// Free a handle obtained from [`jessica_ffi_new`]. Calling with NULL
/// is a no-op. Double-free is undefined behaviour.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn jessica_ffi_free(handle: *mut JessicaHandle) {
    if handle.is_null() {
        return;
    }
    drop(unsafe { Box::from_raw(handle) });
}

/// Free a string returned from [`jessica_ffi_match_intent`] or
/// [`jessica_ffi_version`]. NULL is a no-op.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn jessica_ffi_free_string(ptr: *mut c_char) {
    if ptr.is_null() {
        return;
    }
    drop(unsafe { CString::from_raw(ptr) });
}

/// Library version string (NUL-terminated). The caller owns the
/// returned buffer and MUST free it via [`jessica_ffi_free_string`].
#[unsafe(no_mangle)]
pub extern "C" fn jessica_ffi_version() -> *mut c_char {
    let v = format!("jessica-ffi {} (mobile-core {})",
        env!("CARGO_PKG_VERSION"),
        env!("CARGO_PKG_VERSION"));
    CString::new(v).expect("static string is nul-free").into_raw()
}

// ---------------------------------------------------------------------------
// Intent routing
// ---------------------------------------------------------------------------

/// Load the YAML intent catalogue.
///
/// Returns `JESSICA_OK` on success or one of the `JESSICA_ERR_*`
/// constants on failure. The YAML format matches
/// `blazen_os/configs/intents/system.yaml`.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn jessica_ffi_load_intents(
    handle: *mut JessicaHandle,
    yaml_ptr: *const u8,
    yaml_len: usize,
) -> i32 {
    flatten_status(with_handle(handle, |state| {
        let bytes = unsafe { read_slice(yaml_ptr, yaml_len)? };
        let yaml = std::str::from_utf8(bytes).map_err(|_| JESSICA_ERR_BAD_UTF8)?;
        let router = IntentRouter::from_yaml(yaml).map_err(|_| JESSICA_ERR_BAD_INPUT)?;
        state.router = Some(router);
        Ok(JESSICA_OK)
    }))
}

/// How many intents are currently loaded.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn jessica_ffi_intent_count(handle: *mut JessicaHandle) -> i64 {
    with_handle(handle, |state| {
        Ok(state.router.as_ref().map_or(0, IntentRouter::len) as i64)
    })
    .unwrap_or(-1)
}

/// Match a spoken transcript against the loaded catalogue.
///
/// Returns NULL when no intent matches. On a match, returns a
/// heap-allocated NUL-terminated JSON string the caller owns and
/// must free via [`jessica_ffi_free_string`].
///
/// The JSON shape is the serde-default for [`IntentMatch`]:
/// ```json
/// {"name": "...", "language": "pl", "action": "...", "tool": null, "params": {}, "confirm": "never"}
/// ```
#[unsafe(no_mangle)]
pub unsafe extern "C" fn jessica_ffi_match_intent(
    handle: *mut JessicaHandle,
    transcript_ptr: *const u8,
    transcript_len: usize,
    language_ptr: *const u8,
    language_len: usize,
) -> *mut c_char {
    let result: Result<Option<String>, i32> = with_handle(handle, |state| {
        let transcript_bytes = unsafe { read_slice(transcript_ptr, transcript_len)? };
        let language_bytes = unsafe { read_slice(language_ptr, language_len)? };
        let transcript = std::str::from_utf8(transcript_bytes).map_err(|_| JESSICA_ERR_BAD_UTF8)?;
        let language = std::str::from_utf8(language_bytes).map_err(|_| JESSICA_ERR_BAD_UTF8)?;
        let router = match &state.router {
            Some(r) => r,
            None => return Ok(None),
        };
        let m = match router.match_intent(transcript, language) {
            Some(m) => m,
            None => return Ok(None),
        };
        let json = serde_json::to_string(&m).map_err(|_| JESSICA_ERR_BAD_INPUT)?;
        Ok(Some(json))
    });
    match result {
        Ok(Some(json)) => CString::new(json)
            .map(CString::into_raw)
            .unwrap_or(std::ptr::null_mut()),
        _ => std::ptr::null_mut(),
    }
}

// ---------------------------------------------------------------------------
// Sync log
// ---------------------------------------------------------------------------

/// Merge an incoming fact into the local sync log.
///
/// Returns one of [`JessicaMergeOutcome`] cast to `i32`, or a
/// `JESSICA_ERR_*` value on bad input.
///
/// `fact_json_ptr` must point at a buffer containing a valid
/// [`Fact`](jessica_core::Fact) serialised as JSON.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn jessica_ffi_merge_fact(
    handle: *mut JessicaHandle,
    fact_json_ptr: *const u8,
    fact_json_len: usize,
) -> i32 {
    flatten_status(with_handle(handle, |state| {
        let bytes = unsafe { read_slice(fact_json_ptr, fact_json_len)? };
        let fact: jessica_core::Fact =
            serde_json::from_slice(bytes).map_err(|_| JESSICA_ERR_BAD_INPUT)?;
        Ok(JessicaMergeOutcome::from(state.log.merge(fact)) as i32)
    }))
}

/// Number of distinct facts in the local sync log.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn jessica_ffi_fact_count(handle: *mut JessicaHandle) -> i64 {
    with_handle(handle, |state| Ok(state.log.len() as i64)).unwrap_or(-1)
}

/// Look up a fact by its ULID. Returns NULL when the id is unknown
/// or on bad input. On success, returns a heap-allocated
/// NUL-terminated JSON string the caller owns.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn jessica_ffi_get_fact(
    handle: *mut JessicaHandle,
    id_ptr: *const u8,
    id_len: usize,
) -> *mut c_char {
    let result: Result<Option<String>, i32> = with_handle(handle, |state| {
        let bytes = unsafe { read_slice(id_ptr, id_len)? };
        let id = std::str::from_utf8(bytes).map_err(|_| JESSICA_ERR_BAD_UTF8)?;
        let Some(fact) = state.log.get(id) else {
            return Ok(None);
        };
        let json = serde_json::to_string(fact).map_err(|_| JESSICA_ERR_BAD_INPUT)?;
        Ok(Some(json))
    });
    match result {
        Ok(Some(json)) => CString::new(json)
            .map(CString::into_raw)
            .unwrap_or(std::ptr::null_mut()),
        _ => std::ptr::null_mut(),
    }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Safe handle access: locks the inner state, runs `op`, catches
/// panics. The returned `Result` is left to the caller — entry
/// points that flatten to `i32` use [`flat_status`] below.
pub(crate) fn with_handle<F, R>(handle: *mut JessicaHandle, op: F) -> Result<R, i32>
where
    F: FnOnce(&mut JessicaInner) -> Result<R, i32>,
{
    if handle.is_null() {
        return Err(JESSICA_ERR_BAD_HANDLE);
    }
    let handle = unsafe { &*handle };
    let mut guard = handle.inner.lock().map_err(|_| JESSICA_ERR_BAD_HANDLE)?;
    catch_unwind(AssertUnwindSafe(|| op(&mut guard))).unwrap_or(Err(JESSICA_ERR_PANIC))
}

/// Read a `(*const u8, usize)` buffer into a slice. Treats
/// `(null, 0)` as the empty slice; rejects `(null, N>0)`.
pub(crate) unsafe fn read_slice<'a>(ptr: *const u8, len: usize) -> Result<&'a [u8], i32> {
    if len == 0 {
        return Ok(&[]);
    }
    if ptr.is_null() {
        return Err(JESSICA_ERR_BAD_INPUT);
    }
    Ok(unsafe { std::slice::from_raw_parts(ptr, len) })
}

/// Collapse a `Result<i32, i32>` returned by [`with_handle`] into the
/// flat `i32` status code expected by C ABI entry points.
pub(crate) fn flatten_status(result: Result<i32, i32>) -> i32 {
    result.unwrap_or_else(|code| code)
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use std::ffi::CStr;

    const INTENT_YAML: &str = r#"
version: 1
intents:
  - name: volume_up
    triggers:
      en: ["louder"]
      pl: ["g(ł|l)o(ś|s)niej"]
    action: mutate
"#;

    #[test]
    fn lifecycle_and_match() {
        unsafe {
            let h = jessica_ffi_new();
            assert!(!h.is_null());

            let yaml = INTENT_YAML.as_bytes();
            assert_eq!(
                jessica_ffi_load_intents(h, yaml.as_ptr(), yaml.len()),
                JESSICA_OK
            );
            assert_eq!(jessica_ffi_intent_count(h), 1);

            let transcript = "głośniej".as_bytes();
            let lang = "pl".as_bytes();
            let ptr = jessica_ffi_match_intent(
                h,
                transcript.as_ptr(),
                transcript.len(),
                lang.as_ptr(),
                lang.len(),
            );
            assert!(!ptr.is_null());
            let s = CStr::from_ptr(ptr).to_str().unwrap().to_owned();
            jessica_ffi_free_string(ptr);
            let v: serde_json::Value = serde_json::from_str(&s).unwrap();
            assert_eq!(v["name"], "volume_up");
            assert_eq!(v["language"], "pl");

            jessica_ffi_free(h);
        }
    }

    #[test]
    fn match_returns_null_when_no_intents_loaded() {
        unsafe {
            let h = jessica_ffi_new();
            let t = b"anything";
            let l = b"pl";
            assert!(jessica_ffi_match_intent(h, t.as_ptr(), t.len(), l.as_ptr(), l.len())
                .is_null());
            jessica_ffi_free(h);
        }
    }

    #[test]
    fn null_handle_is_rejected() {
        unsafe {
            let yaml = b"version: 1\nintents: []";
            let rc = jessica_ffi_load_intents(std::ptr::null_mut(), yaml.as_ptr(), yaml.len());
            assert_eq!(rc, JESSICA_ERR_BAD_HANDLE);
        }
    }
}
