package os.blazen.jessica.voice

import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer

/**
 * Thin wrapper around Android's on-device [SpeechRecognizer].
 *
 * One [start] kicks off a single utterance; the wrapper delivers exactly
 * one of [onResult] / [onError] / [onCancelled] back on the main thread,
 * then returns to idle. Re-arm by calling [start] again.
 *
 * Polish is the development default; flip via the `language` argument.
 */
class JessicaAsr(context: Context) {

    private val app = context.applicationContext
    private val mainHandler = Handler(Looper.getMainLooper())

    @Volatile
    private var recognizer: SpeechRecognizer? = null

    /** Final transcript + the language tag actually used. */
    var onResult: ((transcript: String, language: String) -> Unit)? = null

    /** Live partial transcript while the user is still speaking. */
    var onPartial: ((partial: String) -> Unit)? = null

    /** Symbolic error code, e.g. "asr.no_match", "asr.timeout", "asr.network". */
    var onError: ((reason: String) -> Unit)? = null

    /** The user/system cancelled the recognition session. */
    var onCancelled: (() -> Unit)? = null

    fun isOnDeviceAvailable(): Boolean {
        // Modern devices (API 31+) ship with on-device recognition; older
        // ones fall back to the cloud Google service, which still works for
        // the M1 surface even though it violates the on-device contract.
        // (M2 wires whisper.cpp as the true offline fallback.)
        val hasOnDevice = Build.VERSION.SDK_INT >= Build.VERSION_CODES.S &&
            SpeechRecognizer.isOnDeviceRecognitionAvailable(app)
        return hasOnDevice || SpeechRecognizer.isRecognitionAvailable(app)
    }

    fun start(language: String) {
        mainHandler.post {
            val client = ensureRecognizer() ?: run {
                onError?.invoke("asr.unavailable")
                return@post
            }
            val locale = when (language.lowercase()) {
                "en" -> "en-US"
                else -> "pl-PL"
            }
            val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
                putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                putExtra(RecognizerIntent.EXTRA_LANGUAGE, locale)
                putExtra(RecognizerIntent.EXTRA_LANGUAGE_PREFERENCE, locale)
                putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
                putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
                putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, true)
                putExtra(RecognizerIntent.EXTRA_CALLING_PACKAGE, app.packageName)
            }
            client.setRecognitionListener(Listener(language))
            runCatching { client.startListening(intent) }
                .onFailure { onError?.invoke("asr.start_failed") }
        }
    }

    fun cancel() {
        mainHandler.post {
            recognizer?.cancel()
            onCancelled?.invoke()
        }
    }

    fun shutdown() {
        mainHandler.post {
            recognizer?.destroy()
            recognizer = null
        }
    }

    private fun ensureRecognizer(): SpeechRecognizer? {
        if (recognizer != null) return recognizer
        recognizer = when {
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.S &&
                SpeechRecognizer.isOnDeviceRecognitionAvailable(app) ->
                SpeechRecognizer.createOnDeviceSpeechRecognizer(app)
            SpeechRecognizer.isRecognitionAvailable(app) ->
                SpeechRecognizer.createSpeechRecognizer(app)
            else -> null
        }
        return recognizer
    }

    private inner class Listener(private val language: String) : RecognitionListener {
        override fun onReadyForSpeech(params: Bundle?) = Unit
        override fun onBeginningOfSpeech() = Unit
        override fun onRmsChanged(rmsdB: Float) = Unit
        override fun onBufferReceived(buffer: ByteArray?) = Unit
        override fun onEndOfSpeech() = Unit
        override fun onEvent(eventType: Int, params: Bundle?) = Unit

        override fun onPartialResults(partialResults: Bundle?) {
            val transcript = partialResults
                ?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                ?.firstOrNull()
                ?: return
            onPartial?.invoke(transcript)
        }

        override fun onResults(results: Bundle?) {
            val transcript = results
                ?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                ?.firstOrNull()
                ?.takeIf { it.isNotBlank() }
            if (transcript == null) {
                onError?.invoke("asr.no_match")
                return
            }
            onResult?.invoke(transcript, language)
        }

        override fun onError(error: Int) {
            val reason = when (error) {
                SpeechRecognizer.ERROR_NO_MATCH -> "asr.no_match"
                SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> "asr.timeout"
                SpeechRecognizer.ERROR_NETWORK,
                SpeechRecognizer.ERROR_NETWORK_TIMEOUT -> "asr.network"
                SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS -> "asr.permission"
                SpeechRecognizer.ERROR_RECOGNIZER_BUSY -> "asr.busy"
                else -> "asr.error_$error"
            }
            onError?.invoke(reason)
        }
    }
}
