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
/// The reply copy lives in the translation catalog
/// (`Jessica/Resources/L10n.swift`, keys `reply.*` — the Android twin
/// carries the same keys as `reply_*` in `values{,-pl}/strings.xml`).
/// Lookups pass the utterance language explicitly because the reply
/// language is per-utterance, not the device locale.
///
/// Keep replies short — one or two sentences. The user hears them
/// through a speaker; long replies fatigue. If a future intent needs
/// longer output, add a dedicated tool-call branch instead of bloating
/// the catalog.
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
        L10n.reply(intent: intent, language: language)
            ?? L10n.reply(intent: intent, language: "en")
            ?? unknown(language)
    }

    private static func unknown(_ language: String) -> String {
        L10n.replyUnknown(language: language)
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
}
