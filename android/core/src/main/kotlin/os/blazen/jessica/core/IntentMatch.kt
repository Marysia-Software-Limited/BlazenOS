package os.blazen.jessica.core

/**
 * One match result returned by [JessicaCore.matchIntent].
 *
 * Mirrors the `IntentMatch` shape in `domains/jessica-core/src/intent.rs`
 * so a JSON round-trip across JNI is lossless. Treat this file as part of
 * the Rust ↔ Kotlin contract — changes require the matching Rust update.
 */
data class IntentMatch(
    val name: String,
    val language: String,
    val action: String,
    val tool: String? = null,
    val params: Map<String, String> = emptyMap(),
    val confirm: Confirm = Confirm.NEVER,
) {
    enum class Confirm { NEVER, SOFT, HARD }
}
