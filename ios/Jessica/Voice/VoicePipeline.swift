import Observation
import SwiftUI
import JessicaCore

/// Voice-pipeline state machine for the Jessica app.
///
/// Coordinates: wake-word detection → ASR → intent match / Foundation
/// Models → TTS.  Exposed as `@Observable` so SwiftUI views rebuild
/// automatically on state transitions.
///
/// Mirrors `android/app/.../voice/JessicaOrchestrator.kt` — same state
/// names, same language-pinning rules. Per `docs/13-LANGUAGES.md`,
/// PL is the development default; the user can pin PL or EN explicitly
/// (UI toggle OR voice intent `language_pin_pl` / `language_pin_en` /
/// `language_unpin`).
@Observable
@MainActor
final class VoicePipeline {

    enum State: Equatable {
        case idle
        case listening      // wake-word window open
        case recognizing    // capturing utterance via SpeechEngine
        case responding     // Foundation Models thinking
        case speaking       // TTS playing back
        case error(String)

        static func == (lhs: State, rhs: State) -> Bool {
            switch (lhs, rhs) {
            case (.idle, .idle), (.listening, .listening),
                 (.recognizing, .recognizing), (.responding, .responding),
                 (.speaking, .speaking): true
            case (.error(let a), .error(let b)): a == b
            default: false
            }
        }
    }

    /// One completed turn — what the user said, what Jessica replied,
    /// in which language. Persists across state transitions so the UI
    /// can show the last interaction at all times.
    struct Turn: Equatable {
        let transcript: String
        let reply: String
        let language: String
    }

    private(set) var state: State = .idle
    private(set) var transcript: String = ""
    private(set) var lastReply: String = ""
    private(set) var lastTurn: Turn?

    /// Current language tag ("pl" or "en"). Drives ASR locale,
    /// TTS voice, and the canned-reply lookup.
    private(set) var language: String = "pl"

    /// When `true`, the orchestrator ignores ASR's language hint and
    /// stays in `language`. When `false`, the orchestrator follows
    /// ASR's detected language (M2 will add per-utterance auto-detect;
    /// M1 stays in PL by default).
    private(set) var isLanguagePinned: Bool = false

    private let core: JessicaCore
    private let wake = WakeWordDetector()
    private let asr = SpeechEngine()
    private let llm = FoundationModelResponder()
    private let tts = TextToSpeech()
    private var pipelineTask: Task<Void, Never>?

    init(core: JessicaCore) { self.core = core }

    // MARK: - Lifecycle

    func start() {
        guard pipelineTask == nil else { return }
        pipelineTask = Task { await runLoop() }
    }

    func stop() {
        pipelineTask?.cancel()
        pipelineTask = nil
        Task { await wake.stop() }
        tts.stopSpeaking()
        state = .idle
    }

    /// User-facing interrupt: stops TTS, returns to listening. Used by
    /// the "Stop" button while Jessica is speaking. Does NOT cancel
    /// the whole pipeline (use ``stop`` for that).
    func interrupt() {
        tts.stopSpeaking()
        if state != .idle { state = .listening }
    }

    // MARK: - Language pinning

    func pinLanguage(_ lang: String) {
        let normalized = lang.hasPrefix("pl") ? "pl" : "en"
        language = normalized
        isLanguagePinned = true
    }

    func unpinLanguage() {
        isLanguagePinned = false
        language = "pl"
    }

    // MARK: - Main loop

    private func runLoop() async {
        guard await asr.requestPermission() else {
            state = .error(L10n.voicePermissionDenied); return
        }
        await llm.prepare(knownIntents: [])   // M2: pass real intent names from core

        do {
            try await wake.start()
        } catch {
            state = .error(error.localizedDescription); return
        }
        state = .listening

        for await _ in wake.events {
            guard !Task.isCancelled else { break }
            await handleWake()
        }
        state = .idle
    }

    private func handleWake() async {
        state = .recognizing
        transcript = ""

        let asrLocale = isLanguagePinned
            ? Locale(identifier: language == "pl" ? "pl-PL" : "en-US")
            : Locale.current

        let text: String
        do {
            text = try await asr.transcribeUtterance(locale: asrLocale)
        } catch {
            state = .error(error.localizedDescription); return
        }

        guard !text.isEmpty else { state = .listening; return }
        transcript = text

        // ASR's working locale → BCP-47 language tag normalised to 2 chars.
        let asrLanguage = asrLocale.identifier
            .split(separator: "-").first
            .map { String($0).lowercased() }
            ?? "pl"

        await routeUtterance(text, asrLanguage: asrLanguage)
    }

    private func routeUtterance(_ text: String, asrLanguage: String) async {
        // Fast path: local regex matcher in JessicaCore.
        if let match = core.matchIntent(transcript: text, language: asrLanguage) {
            // Voice-driven language control flips the pin before the
            // reply is spoken, so the next utterance uses the new language.
            if match.name.hasPrefix("language_") {
                applyLanguageIntent(match)
            }
            let effective = isLanguagePinned ? language : asrLanguage
            let reply = ReplyGenerator.reply(match: match, language: effective)
            await complete(transcript: text, reply: reply, language: effective)
            return
        }

        // Slow path: on-device Foundation Models (iOS 26+ on A17+).
        state = .responding
        let effective = isLanguagePinned ? language : asrLanguage
        if let result = try? await llm.respond(to: text), !result.reply.isEmpty {
            await complete(transcript: text, reply: result.reply, language: effective)
        } else {
            // Foundation Models unavailable or empty — fall back to the
            // unknown-intent canned reply so the user gets *something*.
            let fallback = ReplyGenerator.reply(match: nil, language: effective)
            await complete(transcript: text, reply: fallback, language: effective)
        }
    }

    private func applyLanguageIntent(_ match: IntentMatch) {
        switch match.name {
        case "language_pin_pl": pinLanguage("pl")
        case "language_pin_en": pinLanguage("en")
        case "language_unpin":  unpinLanguage()
        default: break
        }
    }

    private func complete(transcript: String, reply: String, language: String) async {
        lastReply = reply
        lastTurn = Turn(transcript: transcript, reply: reply, language: language)
        await speak(reply, language: language)
    }

    private func speak(_ text: String, language: String) async {
        state = .speaking
        let bcp47 = language.hasPrefix("pl") ? "pl-PL" : "en-US"
        await tts.speak(text, language: bcp47)
        state = .listening
    }
}
