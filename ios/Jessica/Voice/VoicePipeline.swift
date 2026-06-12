import Observation
import SwiftUI
import JessicaCore

/// Voice-pipeline state machine for the Jessica app.
///
/// Coordinates: wake-word detection → ASR → intent match / Foundation
/// Models → TTS.  Exposed as `@Observable` so SwiftUI views rebuild
/// automatically on state transitions.
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

    private(set) var state: State = .idle
    private(set) var transcript: String = ""
    private(set) var lastReply: String = ""

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

    // MARK: - Main loop

    private func runLoop() async {
        guard await asr.requestPermission() else {
            state = .error(L10n.voicePermissionDenied); return
        }
        await llm.prepare(knownIntents: [])   // M1: expose intent names from core

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

        let text: String
        do {
            text = try await asr.transcribeUtterance(locale: Locale.current)
        } catch {
            state = .error(error.localizedDescription); return
        }

        guard !text.isEmpty else { state = .listening; return }
        transcript = text

        let lang = Locale.preferredLanguages.first
            .flatMap { String($0.prefix(2)) } ?? "pl"

        // Fast path: local regex matcher in JessicaCore.
        if let match = core.matchIntent(transcript: text, language: lang) {
            lastReply = builtinReply(for: match)
            await speak(lastReply, language: match.language)
            return
        }

        // Slow path: on-device Foundation Models.
        state = .responding
        if let result = try? await llm.respond(to: text) {
            lastReply = result.reply
            await speak(lastReply, language: lang)
        } else {
            // Foundation Models unavailable — acknowledge and continue.
            let sorry = lang == "pl"
                ? "Nie rozumiem. Spróbuj ponownie."
                : "I didn't understand. Please try again."
            await speak(sorry, language: lang)
        }
    }

    private func speak(_ text: String, language: String) async {
        state = .speaking
        let bcp47 = language.hasPrefix("pl") ? "pl-PL" : "en-US"
        await tts.speak(text, language: bcp47)
        state = .listening
    }

    // MARK: - Built-in replies for local intents

    private func builtinReply(for match: IntentMatch) -> String {
        let pl = match.language == "pl"
        switch match.name {
        case "volume_up":
            return pl ? "Zwiększam głośność." : "Turning it up."
        case "volume_down":
            return pl ? "Zmniejszam głośność." : "Turning it down."
        case "time_query":
            let fmt = DateFormatter()
            fmt.timeStyle = .short
            fmt.locale = Locale(identifier: pl ? "pl-PL" : "en-US")
            let t = fmt.string(from: Date())
            return pl ? "Jest godzina \(t)." : "It's \(t)."
        default:
            return pl ? "Zrobione." : "Done."
        }
    }
}
