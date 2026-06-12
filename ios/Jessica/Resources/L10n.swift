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

    // MARK: Settings
    static var settingsTitle: String            { localized("settings.title") }
    static var settingsDone: String             { localized("settings.done") }
    static var settingsGeminiSection: String    { localized("settings.gemini.section") }
    static var settingsGeminiPlaceholder: String { localized("settings.gemini.placeholder") }
    static var settingsGeminiSave: String       { localized("settings.gemini.save") }
    static var settingsGeminiClear: String      { localized("settings.gemini.clear") }
    static var settingsGeminiSaved: String      { localized("settings.gemini.saved") }
    static var settingsGeminiFooter: String     { localized("settings.gemini.footer") }
    static var settingsMemorySection: String    { localized("settings.memory.section") }
    static var settingsMemoryEmpty: String      { localized("settings.memory.empty") }
    static var settingsMemoryClear: String      { localized("settings.memory.clear") }
    static var settingsMemoryConfirmClear: String { localized("settings.memory.confirm_clear") }
    static var settingsRemindersSection: String { localized("settings.reminders.section") }
    static var settingsRemindersEmpty: String   { localized("settings.reminders.empty") }
    static var settingsRemindersClear: String   { localized("settings.reminders.clear") }
    static var settingsRemindersConfirmClear: String { localized("settings.reminders.confirm_clear") }
    static var settingsAboutSection: String     { localized("settings.about.section") }
    static var settingsAboutVersion: String     { localized("settings.about.version") }
    static var settingsAboutIntents: String     { localized("settings.about.intents") }

    // MARK: - Lookup

    private static let strings: [String: [String: String]] = [
        "pl": [
            "home.greeting":             "Cześć, jestem Jessica.",
            "home.status.intents":       "Załadowano %lld intencji.",
            "home.listenHint":           "Powiedz \"hej Jessico\", żeby mnie obudzić.",

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

            "settings.title":               "Ustawienia",
            "settings.done":                "Gotowe",
            "settings.gemini.section":      "Gemini (chmura, opcjonalnie)",
            "settings.gemini.placeholder":  "Klucz API",
            "settings.gemini.save":         "Zapisz",
            "settings.gemini.clear":        "Usuń",
            "settings.gemini.saved":        "Klucz zapisany w pęku kluczy.",
            "settings.gemini.footer":       "Klucz służy do zapytań o wiadomości i pytań spoza katalogu. Bez klucza Jessica używa tylko modelu na urządzeniu.",
            "settings.memory.section":      "Pamięć",
            "settings.memory.empty":        "Nic jeszcze nie zapamiętałam.",
            "settings.memory.clear":        "Wyczyść pamięć",
            "settings.memory.confirm_clear": "Wyczyścić wszystkie zapisane fakty?",
            "settings.reminders.section":   "Przypomnienia",
            "settings.reminders.empty":     "Brak nadchodzących przypomnień.",
            "settings.reminders.clear":     "Anuluj wszystkie",
            "settings.reminders.confirm_clear": "Anulować wszystkie przypomnienia?",
            "settings.about.section":       "O aplikacji",
            "settings.about.version":       "Wersja",
            "settings.about.intents":       "Załadowane intencje",
        ],
        "en": [
            "home.greeting":             "Hi, I'm Jessica.",
            "home.status.intents":       "%lld intents loaded.",
            "home.listenHint":           "Say \"hey Jessica\" to wake me.",

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

            "settings.title":               "Settings",
            "settings.done":                "Done",
            "settings.gemini.section":      "Gemini (cloud, optional)",
            "settings.gemini.placeholder":  "API key",
            "settings.gemini.save":         "Save",
            "settings.gemini.clear":        "Remove",
            "settings.gemini.saved":        "Key saved to Keychain.",
            "settings.gemini.footer":       "Used for news queries and questions outside the catalogue. Without a key Jessica answers only from the on-device model.",
            "settings.memory.section":      "Memory",
            "settings.memory.empty":        "Nothing remembered yet.",
            "settings.memory.clear":        "Clear memory",
            "settings.memory.confirm_clear": "Clear all remembered facts?",
            "settings.reminders.section":   "Reminders",
            "settings.reminders.empty":     "No upcoming reminders.",
            "settings.reminders.clear":     "Cancel all",
            "settings.reminders.confirm_clear": "Cancel all reminders?",
            "settings.about.section":       "About",
            "settings.about.version":       "Version",
            "settings.about.intents":       "Loaded intents",
        ],
    ]

    private static func localized(_ key: String) -> String {
        let lang = Locale.preferredLanguages.first
            .flatMap { $0.split(separator: "-").first }
            .map(String.init)?.lowercased() ?? "pl"
        return strings[lang]?[key] ?? strings["en"]?[key] ?? key
    }
}
