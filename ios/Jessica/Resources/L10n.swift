import Foundation

/// Strongly-typed PL + EN strings.
///
/// M0: hand-rolled dictionary keyed off the current locale.
/// M1: migrate to a `.xcstrings` string catalogue once the spec
///     stabilises (see `LOCALIZATION_PREFERS_STRING_CATALOGS` in
///     `project.yml`).  The PL+EN parity rule still applies.
///
/// Adding a new key requires entries in both `pl` and `en`. A change
/// that only adds the English copy is incomplete (see
/// `docs/13-LANGUAGES.md` in the monorepo root). The Android twin
/// mirrors the same keys (snake_case form) in
/// `android/app/src/main/res/values{,-pl}/strings.xml`.
enum L10n {
    // MARK: Home
    static var homeGreeting: String      { localized("home.greeting") }
    static var homeListenHint: String    { localized("home.listenHint") }
    static func homeStatusIntents(_ n: Int64) -> String {
        String(format: localized("home.status.intents"), n)
    }

    // MARK: Voice pipeline state
    static var voiceIdle: String            { localized("voice.idle") }
    static var voiceListening: String       { localized("voice.listening") }
    static var voiceRecognizing: String     { localized("voice.recognizing") }
    static var voiceResponding: String      { localized("voice.responding") }
    static var voiceSpeaking: String        { localized("voice.speaking") }
    static var voiceError: String           { localized("voice.error") }
    static var voiceStart: String           { localized("voice.start") }
    static var voiceStop: String            { localized("voice.stop") }
    static var voiceInterrupt: String       { localized("voice.interrupt") }
    static var voicePermissionDenied: String { localized("voice.permissionDenied") }

    // MARK: Last turn
    static var lastTurnYou: String          { localized("last_turn.you") }
    static var lastTurnJessica: String      { localized("last_turn.jessica") }

    // MARK: Language toggle
    static var languagePinned: String       { localized("language.pinned") }
    static var languageAuto: String         { localized("language.auto") }
    static var languagePL: String           { localized("language.pl") }
    static var languageEN: String           { localized("language.en") }
    static var languageAutoButton: String   { localized("language.auto_button") }

    // MARK: Permission gate
    static var permissionRationaleTitle: String { localized("permission.rationale_title") }
    static var permissionRationaleBody: String  { localized("permission.rationale_body") }
    static var permissionOpenSettings: String   { localized("permission.open_settings") }

    // MARK: - Lookup

    private static let strings: [String: [String: String]] = [
        "pl": [
            "home.greeting":             "Cześć, jestem Jessica.",
            "home.status.intents":       "Załadowano %lld intencji.",
            "home.listenHint":           "Stuknij w mikrofon i o coś zapytaj.",

            "voice.idle":                "Bezczynna",
            "voice.listening":           "Słucham…",
            "voice.recognizing":         "Rozpoznaję…",
            "voice.responding":          "Myślę…",
            "voice.speaking":            "Mówię…",
            "voice.error":               "Błąd",
            "voice.start":               "Uruchom Jessicę",
            "voice.stop":                "Zatrzymaj",
            "voice.interrupt":           "Stop",
            "voice.permissionDenied":    "Brak dostępu do mikrofonu.",

            "last_turn.you":             "Ty: %@",
            "last_turn.jessica":         "Jessica: %@",

            "language.pinned":           "Język: %@ (przypięty)",
            "language.auto":             "Język: %@ (auto)",
            "language.pl":               "PL",
            "language.en":               "EN",
            "language.auto_button":      "Auto",

            "permission.rationale_title": "Jessica potrzebuje mikrofonu",
            "permission.rationale_body":  "Nasłuchuje Twojego głosu na urządzeniu, więc dźwięk nigdy nie opuszcza telefonu. Bez mikrofonu nie odpowie.",
            "permission.open_settings":   "Otwórz Ustawienia",
        ],
        "en": [
            "home.greeting":             "Hi, I'm Jessica.",
            "home.status.intents":       "%lld intents loaded.",
            "home.listenHint":           "Tap the mic and ask me something.",

            "voice.idle":                "Idle",
            "voice.listening":           "Listening…",
            "voice.recognizing":         "Recognizing…",
            "voice.responding":          "Thinking…",
            "voice.speaking":            "Speaking…",
            "voice.error":               "Error",
            "voice.start":               "Start Jessica",
            "voice.stop":                "Stop",
            "voice.interrupt":           "Stop",
            "voice.permissionDenied":    "Microphone access denied.",

            "last_turn.you":             "You: %@",
            "last_turn.jessica":         "Jessica: %@",

            "language.pinned":           "Language: %@ (pinned)",
            "language.auto":             "Language: %@ (auto)",
            "language.pl":               "PL",
            "language.en":               "EN",
            "language.auto_button":      "Auto",

            "permission.rationale_title": "Jessica needs the microphone",
            "permission.rationale_body":  "She listens for your voice on-device, so the audio never leaves the phone. Without the mic she can't answer you.",
            "permission.open_settings":   "Open Settings",
        ],
    ]

    private static func localized(_ key: String) -> String {
        let lang = Locale.preferredLanguages.first
            .flatMap { $0.split(separator: "-").first }
            .map(String.init)?.lowercased() ?? "pl"
        return strings[lang]?[key] ?? strings["en"]?[key] ?? key
    }
}
