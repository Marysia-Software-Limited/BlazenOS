package os.blazen.jessica.voice

import android.content.Context
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import java.util.Locale
import java.util.UUID

/**
 * Thin wrapper around [TextToSpeech]. PL is the development default; EN
 * switches via [speak]'s `language` argument.
 *
 * M1 ships with the OEM voice (Google Speech Service "premium" voices on
 * supported devices). M2 wires Personal-Voice-equivalent profiles when
 * Android exposes them.
 */
class JessicaTts(context: Context) {

    private val app = context.applicationContext

    @Volatile
    private var tts: TextToSpeech? = null

    @Volatile
    private var ready: Boolean = false

    private val pendingActions = mutableListOf<() -> Unit>()

    /** Callback invoked on the main thread when speech finishes (success or error). */
    var onDone: (() -> Unit)? = null
    var onError: ((reason: String) -> Unit)? = null

    /** Build the engine on demand; safe to call repeatedly. */
    fun warmUp() {
        if (tts != null) return
        tts = TextToSpeech(app) { status ->
            ready = status == TextToSpeech.SUCCESS
            if (ready) {
                tts?.setOnUtteranceProgressListener(progressListener)
                drainPending()
            } else {
                onError?.invoke("tts.init_failed")
            }
        }
    }

    /** Speak `text` in `language` ("pl" or "en"). Falls back to PL if unsupported. */
    fun speak(text: String, language: String) {
        val action = action@ {
            val engine = tts ?: return@action
            val locale = when (language.lowercase()) {
                "en" -> Locale.ENGLISH
                else -> Locale("pl", "PL")
            }
            val rc = engine.setLanguage(locale)
            if (rc == TextToSpeech.LANG_MISSING_DATA || rc == TextToSpeech.LANG_NOT_SUPPORTED) {
                engine.language = Locale("pl", "PL")
            }
            val utteranceId = UUID.randomUUID().toString()
            engine.speak(text, TextToSpeech.QUEUE_FLUSH, null, utteranceId)
        }
        if (ready) action() else pendingActions += action
    }

    fun stop() {
        tts?.stop()
    }

    fun shutdown() {
        tts?.stop()
        tts?.shutdown()
        tts = null
        ready = false
        pendingActions.clear()
    }

    private fun drainPending() {
        val snapshot = pendingActions.toList()
        pendingActions.clear()
        snapshot.forEach { it.invoke() }
    }

    private val progressListener = object : UtteranceProgressListener() {
        override fun onStart(utteranceId: String?) = Unit
        override fun onDone(utteranceId: String?) {
            onDone?.invoke()
        }
        @Deprecated("legacy callback")
        override fun onError(utteranceId: String?) {
            onError?.invoke("tts.utterance_error")
        }
        override fun onError(utteranceId: String?, errorCode: Int) {
            onError?.invoke("tts.utterance_error_$errorCode")
        }
    }
}
