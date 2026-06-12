import Foundation
import FoundationModels

/// Result of processing an utterance through the on-device LLM.
@Generable(description: "Intent classification and reply from the Jessica assistant")
struct IntentResult: Sendable {
    @Guide(description: "The intent name from the loaded catalogue, or 'unknown' if none matched")
    var intent: String

    @Guide(description: "A brief, natural spoken reply in the same language the user spoke")
    var reply: String
}

/// Processes recognised speech using Apple Intelligence Foundation Models
/// (`SystemLanguageModel.default`, on-device ~3B model on A17 Pro / A18 / M2+).
actor FoundationModelResponder {

    private var session: LanguageModelSession?

    var isAvailable: Bool { SystemLanguageModel.default.isAvailable }

    /// (Re)build the session with the current intent catalogue.
    func prepare(knownIntents: [String]) {
        guard isAvailable else { return }
        let locale = Locale.current.identifier
        let list = knownIntents.isEmpty
            ? "volume_up, volume_down, time_query"
            : knownIntents.joined(separator: ", ")
        session = LanguageModelSession(instructions: """
            You are Jessica, a private voice assistant running entirely on-device.
            The person's locale is \(locale).
            ALWAYS respond in the language the person spoke.
            Known intent names: [\(list)].
            Match the utterance to the closest intent name, or use 'unknown'.
            Respond with a brief, natural spoken reply — one sentence max.
            """)
    }

    /// Returns a structured reply, or `nil` when the model is unavailable.
    func respond(to utterance: String) async throws -> IntentResult? {
        guard isAvailable, let session else { return nil }
        return try await session.respond(to: utterance,
                                         generating: IntentResult.self)
    }
}
