package os.blazen.jessica.voice

import android.content.Context
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import os.blazen.jessica.brain.ReplyGenerator
import os.blazen.jessica.core.IntentMatch
import os.blazen.jessica.core.JessicaCore

/**
 * Drives the voice loop: Idle → Listening → Thinking → Speaking → Idle.
 *
 * The orchestrator owns the [JessicaCore] handle and is the only place
 * that talks to [JessicaAsr] and [JessicaTts]. UI code only reads
 * [state] and calls [tap], [pinLanguage], [unpinLanguage], or
 * [interrupt].
 *
 * Language policy (matches `docs/13-LANGUAGES.md`):
 *   - PL is the default.
 *   - The user can pin to PL or EN via UI or voice command
 *     (`language_pin_pl` / `language_pin_en`).
 *   - When unpinned, the orchestrator stays in PL until the user pins
 *     EN explicitly. (M2 will add per-utterance auto-detect.)
 */
class JessicaOrchestrator(
    context: Context,
    private val core: JessicaCore,
) {

    private val tts = JessicaTts(context)
    private val asr = JessicaAsr(context)

    private val _state = MutableStateFlow<JessicaState>(JessicaState.Idle)
    val state: StateFlow<JessicaState> = _state.asStateFlow()

    private val _language = MutableStateFlow("pl")
    /** Current language tag ("pl" or "en"). */
    val language: StateFlow<String> = _language.asStateFlow()

    private val _isLanguagePinned = MutableStateFlow(false)
    val isLanguagePinned: StateFlow<Boolean> = _isLanguagePinned.asStateFlow()

    private val _lastTurn = MutableStateFlow<Turn?>(null)
    /** The most recent (transcript, reply) pair the user can re-read. */
    val lastTurn: StateFlow<Turn?> = _lastTurn.asStateFlow()

    init {
        tts.onDone = { _state.value = JessicaState.Idle }
        tts.onError = { reason ->
            _state.value = JessicaState.Error(reason)
            _state.value = JessicaState.Idle
        }

        asr.onResult = ::handleTranscript
        asr.onPartial = { partial ->
            (state.value as? JessicaState.Listening)?.let {
                _state.value = it.copy(partial = partial)
            }
        }
        asr.onError = { reason ->
            _state.value = JessicaState.Error(reason)
            _state.value = JessicaState.Idle
        }
        asr.onCancelled = { _state.value = JessicaState.Idle }

        // Kick off the (async) TTS engine bind now that callbacks are wired.
        tts.warmUp()
    }

    /** Called by the mic button. State-machine-aware: Idle starts a turn, anything else interrupts. */
    fun tap() {
        when (state.value) {
            JessicaState.Idle -> startListening()
            is JessicaState.Speaking -> interrupt()
            is JessicaState.Listening -> asr.cancel()
            is JessicaState.Thinking, is JessicaState.Error -> Unit
        }
    }

    /** User-facing interrupt: stops TTS, cancels ASR, returns to Idle. */
    fun interrupt() {
        tts.stop()
        asr.cancel()
        _state.value = JessicaState.Idle
    }

    fun pinLanguage(lang: String) {
        _language.value = lang
        _isLanguagePinned.value = true
    }

    fun unpinLanguage() {
        _isLanguagePinned.value = false
        _language.value = "pl"
    }

    fun shutdown() {
        asr.shutdown()
        tts.shutdown()
    }

    // ---------------------------------------------------------------------
    // Internals
    // ---------------------------------------------------------------------

    private fun startListening() {
        if (!asr.isOnDeviceAvailable()) {
            _state.value = JessicaState.Error("asr.unavailable")
            _state.value = JessicaState.Idle
            return
        }
        _state.value = JessicaState.Listening(language.value, partial = "")
        asr.start(language.value)
    }

    private fun handleTranscript(transcript: String, asrLanguage: String) {
        _state.value = JessicaState.Thinking(transcript, asrLanguage)
        val match: IntentMatch? = core.matchIntent(transcript, asrLanguage)

        // Voice-driven language control bypasses the canned reply path so
        // the next utterance immediately respects the pin.
        if (match != null && match.name.startsWith("language_")) {
            applyLanguageIntent(match)
        }

        val effectiveLanguage = if (isLanguagePinned.value) _language.value else asrLanguage
        val reply = ReplyGenerator.reply(match, effectiveLanguage)
        _lastTurn.value = Turn(transcript, reply, effectiveLanguage)
        _state.value = JessicaState.Speaking(reply, effectiveLanguage)
        tts.speak(reply, effectiveLanguage)
    }

    private fun applyLanguageIntent(match: IntentMatch) {
        when (match.name) {
            "language_pin_pl" -> pinLanguage("pl")
            "language_pin_en" -> pinLanguage("en")
            "language_unpin" -> unpinLanguage()
        }
    }
}

/** Coarse-grained UI state. */
sealed interface JessicaState {
    data object Idle : JessicaState
    data class Listening(val language: String, val partial: String) : JessicaState
    data class Thinking(val transcript: String, val language: String) : JessicaState
    data class Speaking(val text: String, val language: String) : JessicaState
    data class Error(val reason: String) : JessicaState
}

/** A completed turn: what the user said, what Jessica replied, in which language. */
data class Turn(val transcript: String, val reply: String, val language: String)
