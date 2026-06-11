package os.blazen.jessica.core

/**
 * Idiomatic Kotlin façade over [JessicaCoreNative]. UI code should only
 * ever talk to this class; the JNI surface stays internal.
 *
 * M0 fallback: when [JessicaCoreNative.LIB_AVAILABLE] is false, the
 * façade runs a tiny pure-Kotlin intent matcher so the UI can still
 * exercise the API contract end-to-end on a dev laptop.
 *
 * M1: when LIB_AVAILABLE flips true, [matchIntent] needs a JSON parser
 * for the FFI's serde output — add kotlinx.serialization to `:core`'s
 * Gradle deps and wire it here. The contract under test (intent names,
 * params, confirm levels) doesn't change.
 */
class JessicaCore private constructor(
    private val handle: Long,
    private val native: Boolean,
) {

    private val fallback = if (!native) PureKotlinIntents() else null

    /** Load the YAML intent catalogue. Returns true on success. */
    fun loadIntents(yaml: String): Boolean {
        if (native) {
            val rc = JessicaCoreNative.nativeLoadIntents(handle, yaml.toByteArray(Charsets.UTF_8))
            return rc == JESSICA_OK
        }
        return fallback!!.load(yaml)
    }

    /** Match a transcript against the loaded catalogue. Returns null when no intent fires. */
    fun matchIntent(transcript: String, language: String): IntentMatch? {
        if (native) {
            JessicaCoreNative.nativeMatchIntent(handle, transcript, language)
                ?: return null
            throw NotImplementedError(
                "M1: parse the FFI JSON output here once kotlinx.serialization is on :core's classpath."
            )
        }
        return fallback!!.match(transcript, language)
    }

    /** How many intents are currently loaded. */
    fun intentCount(): Long {
        if (native) return JessicaCoreNative.nativeIntentCount(handle)
        return fallback!!.count().toLong()
    }

    /** Release the underlying handle. Idempotent. */
    fun close() {
        if (native) JessicaCoreNative.nativeFree(handle)
    }

    companion object {
        fun create(): JessicaCore {
            val useNative = JessicaCoreNative.LIB_AVAILABLE
            val handle = if (useNative) JessicaCoreNative.nativeNew() else 0L
            return JessicaCore(handle = handle, native = useNative)
        }
    }
}

internal const val JESSICA_OK = 0
internal const val JESSICA_ERR_BAD_HANDLE = -1
internal const val JESSICA_ERR_BAD_UTF8 = -2
internal const val JESSICA_ERR_BAD_INPUT = -3
internal const val JESSICA_ERR_PANIC = -99
