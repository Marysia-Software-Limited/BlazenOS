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
    flatten_status, jessica_ffi_free, jessica_ffi_load_intents, jessica_ffi_match_intent,
    jessica_ffi_merge_fact, jessica_ffi_new, with_handle, JessicaHandle, JESSICA_ERR_BAD_INPUT,
};

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
    if ptr.is_null() {
        return std::ptr::null_mut();
    }
    let cstr = unsafe { std::ffi::CStr::from_ptr(ptr) };
    let result = match cstr.to_str() {
        Ok(s) => env
            .new_string(s)
            .ok()
            .map(|j| j.into_raw())
            .unwrap_or(std::ptr::null_mut()),
        Err(_) => std::ptr::null_mut(),
    };
    // Free the Rust-side string now that we've copied it across the boundary.
    unsafe { crate::jessica_ffi_free_string(ptr) }
    result
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

// Make the unused-import warning happy in non-android builds.
#[allow(dead_code)]
fn _unused() {
    let _ = flatten_status;
}
