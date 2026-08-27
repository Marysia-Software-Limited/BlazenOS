package os.blazen.jessica.brain

import android.content.Context
import android.content.res.Configuration
import android.content.res.Resources
import os.blazen.jessica.R
import os.blazen.jessica.core.IntentMatch
import java.util.Locale

/**
 * Maps an [IntentMatch] (or its absence) to a spoken reply.
 *
 * M1: canned, fully-bilingual replies per known intent. M2 hands the
 * "unknown intent" branch off to Gemini Nano via AICore (Pixel 8+ /
 * Samsung S24+), behind a `Build.VERSION.SDK_INT >= 36` guard.
 *
 * The reply copy lives in the translation catalog
 * (`res/values/strings.xml` + `res/values-pl/strings.xml`, keys
 * `reply_*`). Because the reply language is chosen **per utterance**
 * (docs/13-LANGUAGES.md), not by device locale, lookups go through
 * locale-pinned [Resources] built once per language, never through the
 * ambient configuration.
 *
 * Keep replies short — one or two sentences. The user hears them through
 * a speaker; long replies fatigue. If a future intent needs longer
 * output, add a dedicated tool-call branch instead of bloating this map.
 */
class ReplyGenerator(context: Context) {

    private val plResources = localizedResources(context, "pl")
    private val enResources = localizedResources(context, "en")

    fun reply(match: IntentMatch?, language: String): String =
        match?.let { canned(it.name, it.language) }
            ?: unknown(language)

    private fun canned(intent: String, language: String): String {
        val id = REPLY_IDS[intent] ?: return unknown(language)
        return resourcesFor(language).getString(id)
    }

    private fun unknown(language: String): String =
        resourcesFor(language).getString(R.string.reply_unknown)

    private fun resourcesFor(language: String): Resources =
        if (language.startsWith("pl")) plResources else enResources

    private companion object {

        val REPLY_IDS = mapOf(
            "volume_up" to R.string.reply_volume_up,
            "volume_down" to R.string.reply_volume_down,
            "time_query" to R.string.reply_time_query,
            "language_pin_pl" to R.string.reply_language_pin_pl,
            "language_pin_en" to R.string.reply_language_pin_en,
            "language_unpin" to R.string.reply_language_unpin,
            "what_can_you_do" to R.string.reply_what_can_you_do,
            "stop" to R.string.reply_stop,
        )

        fun localizedResources(context: Context, language: String): Resources {
            val configuration = Configuration(context.resources.configuration)
            configuration.setLocale(Locale.forLanguageTag(language))
            return context.createConfigurationContext(configuration).resources
        }
    }
}
