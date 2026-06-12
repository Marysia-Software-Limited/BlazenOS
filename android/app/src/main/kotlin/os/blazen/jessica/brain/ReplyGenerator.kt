package os.blazen.jessica.brain

import os.blazen.jessica.core.IntentMatch

/**
 * Maps an [IntentMatch] (or its absence) to a spoken reply.
 *
 * M1: canned, fully-bilingual replies per known intent. M2 hands the
 * "unknown intent" branch off to Gemini Nano via AICore (Pixel 8+ /
 * Samsung S24+), behind a `Build.VERSION.SDK_INT >= 36` guard.
 *
 * Keep replies short — one or two sentences. The user hears them through
 * a speaker; long replies fatigue. If a future intent needs longer
 * output, add a dedicated tool-call branch instead of bloating this map.
 */
object ReplyGenerator {

    fun reply(match: IntentMatch?, language: String): String =
        match?.let { canned(it.name, it.language) }
            ?: unknown(language)

    private fun canned(intent: String, language: String): String {
        val table = if (language.startsWith("pl")) PL_REPLIES else EN_REPLIES
        return table[intent] ?: unknown(language)
    }

    private fun unknown(language: String): String =
        if (language.startsWith("pl")) PL_UNKNOWN else EN_UNKNOWN

    private val PL_REPLIES = mapOf(
        "volume_up" to "Robi się głośniej.",
        "volume_down" to "Robi się ciszej.",
        "time_query" to "Sprawdzę zegar i powiem za chwilę.",
        "language_pin_pl" to "Mówimy po polsku.",
        "language_pin_en" to "Switching to English.",
        "language_unpin" to "Słucham uważnie — wybiorę język sama.",
        "what_can_you_do" to "Mogę zmienić głośność, sprawdzić godzinę albo przeczytać wiadomości.",
        "stop" to "Zamilkam.",
    )

    private val EN_REPLIES = mapOf(
        "volume_up" to "Turning it up.",
        "volume_down" to "Turning it down.",
        "time_query" to "Let me check the time for you.",
        "language_pin_pl" to "Mówimy po polsku.",
        "language_pin_en" to "Switching to English.",
        "language_unpin" to "I'll listen and pick the language myself.",
        "what_can_you_do" to "I can change the volume, check the time, or read the news.",
        "stop" to "Going quiet.",
    )

    private const val PL_UNKNOWN = "Nie rozumiem jeszcze tego polecenia. Powiedz inaczej?"
    private const val EN_UNKNOWN = "I don't have that one wired up yet. Try again?"
}
