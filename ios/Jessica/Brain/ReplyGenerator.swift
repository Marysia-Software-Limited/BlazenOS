import Foundation
import JessicaCore

/// Maps an ``IntentMatch`` (or its absence) to a spoken reply.
///
/// M1: canned, fully-bilingual replies per known intent — mirrors
/// `android/app/.../brain/ReplyGenerator.kt` so the two surfaces stay
/// in lockstep. The Foundation Models path in
/// ``FoundationModelResponder`` takes over for utterances that don't
/// hit a registered intent.
///
/// Keep replies short — one or two sentences. The user hears them
/// through a speaker; long replies fatigue. If a future intent needs
/// longer output, add a dedicated tool-call branch instead of bloating
/// these tables.
enum ReplyGenerator {

    /// `match == nil` → "I don't know that yet" reply in `language`.
    /// `match != nil` → look up the canned reply for `match.name`
    /// in `language` (falls back to English, then the unknown reply).
    ///
    /// `time_query` is special-cased to inject the current time —
    /// the Pi 5 appliance does the same in `blazend.brain.reply`.
    static func reply(match: IntentMatch?, language: String) -> String {
        guard let match else { return unknown(language) }
        if match.name == "time_query" {
            return timeReply(language: language)
        }
        return canned(intent: match.name, language: language)
    }

    private static func canned(intent: String, language: String) -> String {
        let table = language.hasPrefix("pl") ? plReplies : enReplies
        return table[intent]
            ?? enReplies[intent]
            ?? unknown(language)
    }

    private static func unknown(_ language: String) -> String {
        language.hasPrefix("pl") ? plUnknown : enUnknown
    }

    private static func timeReply(language: String) -> String {
        let formatter = DateFormatter()
        formatter.timeStyle = .short
        formatter.locale = Locale(identifier: language.hasPrefix("pl") ? "pl_PL" : "en_US")
        let now = formatter.string(from: Date())
        return language.hasPrefix("pl")
            ? "Jest \(now)."
            : "It's \(now)."
    }

    private static let plReplies: [String: String] = [
        "volume_up": "Robi się głośniej.",
        "volume_down": "Robi się ciszej.",
        "language_pin_pl": "Mówimy po polsku.",
        "language_pin_en": "Switching to English.",
        "language_unpin": "Słucham uważnie — wybiorę język sama.",
        "what_can_you_do": "Mogę zmienić głośność, sprawdzić godzinę albo przeczytać wiadomości.",
        "stop": "Zamilkam.",
    ]

    private static let enReplies: [String: String] = [
        "volume_up": "Turning it up.",
        "volume_down": "Turning it down.",
        "language_pin_pl": "Mówimy po polsku.",
        "language_pin_en": "Switching to English.",
        "language_unpin": "I'll listen and pick the language myself.",
        "what_can_you_do": "I can change the volume, check the time, or read the news.",
        "stop": "Going quiet.",
    ]

    private static let plUnknown = "Nie rozumiem jeszcze tego polecenia. Powiedz inaczej?"
    private static let enUnknown = "I don't have that one wired up yet. Try again?"
}
