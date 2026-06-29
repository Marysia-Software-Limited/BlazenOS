//! JNI entry points called from
//! `os.blazen.jessica.core.JessicaCoreNative`.
//!
//! The Kotlin side declares thin `external fun`s that match these
//! signatures; load with `System.loadLibrary("jessica_ffi")`.
//! Implementation just forwards into the C ABI.

#![allow(non_snake_case)]

use jni::objects::{JByteArray, JClass, JString};
use jni::sys::{jint, jlong, jstring};
use jni::JNIEnv;

use crate::{
    flatten_status, jessica_ffi_add_note, jessica_ffi_free, jessica_ffi_load_intents,
    jessica_ffi_match_intent, jessica_ffi_merge_fact, jessica_ffi_new, jessica_ffi_note_count,
    jessica_ffi_recall_notes, with_handle, JessicaHandle, JESSICA_ERR_BAD_INPUT,
};

/// Copy a Rust-owned C string across the JNI boundary and free it.
/// NULL in → NULL (Java `null`) out — the same "nothing / bad input"
/// signal the C ABI uses.
fn take_cstring(env: &mut JNIEnv, ptr: *mut std::ffi::c_char) -> jstring {
    if ptr.is_null() {
        return std::ptr::null_mut();
    }
    let out = match unsafe { std::ffi::CStr::from_ptr(ptr) }.to_str() {
        Ok(s) => env
            .new_string(s)
            .ok()
            .map(|j| j.into_raw())
            .unwrap_or(std::ptr::null_mut()),
        Err(_) => std::ptr::null_mut(),
    };
    // Free the Rust-side string now that we've copied it across the boundary.
    unsafe { crate::jessica_ffi_free_string(ptr) };
    out
}

/// `nativeNew(): Long` — allocates a new handle.
#[unsafe(no_mangle)]
pub extern "system" fn Java_os_blazen_jessica_core_JessicaCoreNative_nativeNew(
    _env: JNIEnv,
    _class: JClass,
) -> jlong {
    jessica_ffi_new() as jlong
}

/// `nativeFree(handle: Long)`.
#[unsafe(no_mangle)]
pub extern "system" fn Java_os_blazen_jessica_core_JessicaCoreNative_nativeFree(
    _env: JNIEnv,
    _class: JClass,
    handle: jlong,
) {
    unsafe { jessica_ffi_free(handle as *mut JessicaHandle) }
}

/// `nativeLoadIntents(handle: Long, yaml: ByteArray): Int`.
#[unsafe(no_mangle)]
pub extern "system" fn Java_os_blazen_jessica_core_JessicaCoreNative_nativeLoadIntents(
    mut env: JNIEnv,
    _class: JClass,
    handle: jlong,
    yaml: JByteArray,
) -> jint {
    let Ok(bytes) = env.convert_byte_array(&yaml) else {
        return JESSICA_ERR_BAD_INPUT;
    };
    unsafe { jessica_ffi_load_intents(handle as *mut JessicaHandle, bytes.as_ptr(), bytes.len()) }
}

/// `nativeMatchIntent(handle: Long, transcript: String, language: String): String?`.
#[unsafe(no_mangle)]
pub extern "system" fn Java_os_blazen_jessica_core_JessicaCoreNative_nativeMatchIntent<'a>(
    mut env: JNIEnv<'a>,
    _class: JClass<'a>,
    handle: jlong,
    transcript: JString<'a>,
    language: JString<'a>,
) -> jstring {
    let Ok(t) = env.get_string(&transcript) else {
        return std::ptr::null_mut();
    };
    let Ok(l) = env.get_string(&language) else {
        return std::ptr::null_mut();
    };
    let t: String = t.into();
    let l: String = l.into();
    let ptr = unsafe {
        jessica_ffi_match_intent(
            handle as *mut JessicaHandle,
            t.as_ptr(),
            t.len(),
            l.as_ptr(),
            l.len(),
        )
    };
    take_cstring(&mut env, ptr)
}

/// `nativeMergeFact(handle: Long, factJson: ByteArray): Int`.
#[unsafe(no_mangle)]
pub extern "system" fn Java_os_blazen_jessica_core_JessicaCoreNative_nativeMergeFact(
    mut env: JNIEnv,
    _class: JClass,
    handle: jlong,
    fact_json: JByteArray,
) -> jint {
    let Ok(bytes) = env.convert_byte_array(&fact_json) else {
        return JESSICA_ERR_BAD_INPUT;
    };
    unsafe { jessica_ffi_merge_fact(handle as *mut JessicaHandle, bytes.as_ptr(), bytes.len()) }
}

/// `nativeFactCount(handle: Long): Long`.
#[unsafe(no_mangle)]
pub extern "system" fn Java_os_blazen_jessica_core_JessicaCoreNative_nativeFactCount(
    _env: JNIEnv,
    _class: JClass,
    handle: jlong,
) -> jlong {
    with_handle(handle as *mut JessicaHandle, |state| {
        Ok(state.log.len() as i64)
    })
    .unwrap_or(-1)
}

/// `nativeIntentCount(handle: Long): Long`.
#[unsafe(no_mangle)]
pub extern "system" fn Java_os_blazen_jessica_core_JessicaCoreNative_nativeIntentCount(
    _env: JNIEnv,
    _class: JClass,
    handle: jlong,
) -> jlong {
    with_handle(handle as *mut JessicaHandle, |state| {
        Ok(state
            .router
            .as_ref()
            .map_or(0, jessica_core::IntentRouter::len) as i64)
    })
    .unwrap_or(-1)
}

/// `nativeAddNote(handle: Long, text: String, title: String, now: String): String?`
/// — returns the created note as JSON, or null on bad input.
#[unsafe(no_mangle)]
pub extern "system" fn Java_os_blazen_jessica_core_JessicaCoreNative_nativeAddNote<'a>(
    mut env: JNIEnv<'a>,
    _class: JClass<'a>,
    handle: jlong,
    text: JString<'a>,
    title: JString<'a>,
    now: JString<'a>,
) -> jstring {
    let Ok(text) = env.get_string(&text) else {
        return std::ptr::null_mut();
    };
    let Ok(title) = env.get_string(&title) else {
        return std::ptr::null_mut();
    };
    let Ok(now) = env.get_string(&now) else {
        return std::ptr::null_mut();
    };
    let text: String = text.into();
    let title: String = title.into();
    let now: String = now.into();
    let ptr = unsafe {
        jessica_ffi_add_note(
            handle as *mut JessicaHandle,
            text.as_ptr(),
            text.len(),
            title.as_ptr(),
            title.len(),
            now.as_ptr(),
            now.len(),
        )
    };
    take_cstring(&mut env, ptr)
}

/// `nativeNoteCount(handle: Long): Long`.
#[unsafe(no_mangle)]
pub extern "system" fn Java_os_blazen_jessica_core_JessicaCoreNative_nativeNoteCount(
    _env: JNIEnv,
    _class: JClass,
    handle: jlong,
) -> jlong {
    unsafe { jessica_ffi_note_count(handle as *mut JessicaHandle) }
}

/// `nativeRecallNotes(handle: Long, query: String): String?` — returns a
/// JSON array of notes (empty query → all), or null on bad input.
#[unsafe(no_mangle)]
pub extern "system" fn Java_os_blazen_jessica_core_JessicaCoreNative_nativeRecallNotes<'a>(
    mut env: JNIEnv<'a>,
    _class: JClass<'a>,
    handle: jlong,
    query: JString<'a>,
) -> jstring {
    let Ok(q) = env.get_string(&query) else {
        return std::ptr::null_mut();
    };
    let q: String = q.into();
    let ptr =
        unsafe { jessica_ffi_recall_notes(handle as *mut JessicaHandle, q.as_ptr(), q.len()) };
    take_cstring(&mut env, ptr)
}

// Make the unused-import warning happy in non-android builds.
#[allow(dead_code)]
fn _unused() {
    let _ = flatten_status;
}
