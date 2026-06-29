package os.blazen.jessica.core

/**
 * JNI surface implemented by the Rust crate `jessica-ffi`. The native
 * library is loaded once on first reference to this object; signatures
 * here MUST match `domains/jessica-ffi/src/jni_bridge.rs` byte-for-byte.
 *
 * In M0 this object is **not actually called** — [JessicaCore] returns
 * fixtures so the UI builds end-to-end without cargo-ndk wired in.
 * M1 flips [JessicaCore] to delegate every call here.
 */
internal object JessicaCoreNative {

    /** Toggle in M1 once cargo-ndk builds `libjessica_ffi.so`. */
    const val LIB_AVAILABLE: Boolean = false

    init {
        if (LIB_AVAILABLE) {
            System.loadLibrary("jessica_ffi")
        }
    }

    /** `jessica_ffi_new` — returns an opaque handle. */
    external fun nativeNew(): Long

    /** `jessica_ffi_free` — releases a handle obtained from [nativeNew]. */
    external fun nativeFree(handle: Long)

    /** `jessica_ffi_load_intents` — returns one of the JESSICA_OK / ERR codes. */
    external fun nativeLoadIntents(handle: Long, yaml: ByteArray): Int

    /** `jessica_ffi_match_intent` — returns a JSON [IntentMatch] or null. */
    external fun nativeMatchIntent(handle: Long, transcript: String, language: String): String?

    /** `jessica_ffi_merge_fact` — returns a JessicaMergeOutcome or ERR. */
    external fun nativeMergeFact(handle: Long, factJson: ByteArray): Int

    /** `jessica_ffi_fact_count`. */
    external fun nativeFactCount(handle: Long): Long

    /** `jessica_ffi_intent_count`. */
    external fun nativeIntentCount(handle: Long): Long

    /**
     * `jessica_ffi_add_note` — returns the created note as JSON, or null.
     * `now` is an RFC 3339 timestamp (the core takes its clock injected).
     */
    external fun nativeAddNote(handle: Long, text: String, title: String, now: String): String?

    /** `jessica_ffi_note_count`. */
    external fun nativeNoteCount(handle: Long): Long

    /** `jessica_ffi_recall_notes` — JSON array of notes (empty query → all), or null. */
    external fun nativeRecallNotes(handle: Long, query: String): String?
}
