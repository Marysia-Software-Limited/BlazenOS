package os.blazen.jessica.core

/**
 * M0 fallback intent matcher. Parses a *minimal* subset of the YAML
 * intent catalogue (just enough to drive the UI) and runs a regex
 * match per trigger.
 *
 * **Not** a full YAML parser — only handles the catalogue shape used
 * by `configs/intents/*.yaml`. M1 replaces this with the Rust crate
 * via JNI; the YAML stays the same.
 */
internal class PureKotlinIntents {

    private data class Entry(val name: String, val action: String, val patterns: Map<String, List<Regex>>)

    private val entries = mutableListOf<Entry>()

    fun load(yaml: String): Boolean {
        entries.clear()
        var currentName: String? = null
        var currentAction = "query"
        var currentLang: String? = null
        val triggers = mutableMapOf<String, MutableList<Regex>>()

        fun commit() {
            val n = currentName ?: return
            if (triggers.isNotEmpty()) {
                entries += Entry(
                    name = n,
                    action = currentAction,
                    patterns = triggers.mapValues { it.value.toList() },
                )
            }
        }

        for (raw in yaml.lineSequence()) {
            val line = raw.substringBefore('#').trimEnd()
            if (line.isBlank()) continue
            when {
                line.startsWith("  - name:") -> {
                    commit()
                    currentName = line.substringAfter("name:").trim()
                    currentAction = "query"
                    currentLang = null
                    triggers.clear()
                }
                line.trim().startsWith("action:") -> {
                    currentAction = line.substringAfter("action:").trim()
                }
                line.trim().startsWith("triggers:") -> {
                    currentLang = null
                }
                line.trim().startsWith("en:") || line.trim().startsWith("pl:") -> {
                    val lang = line.trim().substringBefore(':')
                    currentLang = lang
                    val inline = line.substringAfter(':').trim()
                    if (inline.startsWith("[") && inline.endsWith("]")) {
                        triggers.getOrPut(lang) { mutableListOf() }
                            .addAll(parseInlineList(inline).map { it.toCaseInsensitiveRegex() })
                    }
                }
                line.trim().startsWith("- ") && currentLang != null -> {
                    val raw = line.trim().removePrefix("- ").trim('"', '\'')
                    triggers.getOrPut(currentLang!!) { mutableListOf() } +=
                        raw.toCaseInsensitiveRegex()
                }
            }
        }
        commit()
        return entries.isNotEmpty()
    }

    fun match(transcript: String, language: String): IntentMatch? {
        val needle = transcript.lowercase()
        for (e in entries) {
            val patterns = e.patterns[language] ?: continue
            for (p in patterns) {
                if (p.containsMatchIn(needle)) {
                    return IntentMatch(
                        name = e.name,
                        language = language,
                        action = e.action,
                    )
                }
            }
        }
        return null
    }

    fun count(): Int = entries.size

    private fun parseInlineList(text: String): List<String> =
        text.removePrefix("[").removeSuffix("]")
            .split(',')
            .map { it.trim().trim('"', '\'') }
            .filter { it.isNotEmpty() }

    private fun String.toCaseInsensitiveRegex(): Regex =
        Regex(this, RegexOption.IGNORE_CASE)
}
