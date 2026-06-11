import Foundation

/// Strongly-typed PL+EN strings.
///
/// M0: hand-rolled dictionary keyed off the current locale. M1: migrate
/// to a `.xcstrings` string catalogue once the spec stabilises (see
/// `INFOPLIST_KEY_LOCALIZATION_PREFERS_STRING_CATALOGS` in `project.yml`).
///
/// Adding a new key requires entries in both `pl` and `en`. A change
/// that only adds the English copy is incomplete (see
/// `docs/13-LANGUAGES.md` in the monorepo root).
enum L10n {
    static var homeGreeting: String { localized("home.greeting") }
    static var homeListenHint: String { localized("home.listenHint") }

    static func homeStatusIntents(_ n: Int64) -> String {
        String(format: localized("home.status.intents"), n)
    }

    // MARK: - Lookup

    private static let strings: [String: [String: String]] = [
        "pl": [
            "home.greeting": "Cześć, jestem Jessica.",
            "home.status.intents": "Załadowano %lld intencji.",
            "home.listenHint": "Powiedz „hej Jessico”, żeby mnie obudzić.",
        ],
        "en": [
            "home.greeting": "Hi, I'm Jessica.",
            "home.status.intents": "%lld intents loaded.",
            "home.listenHint": "Say \"hey Jessica\" to wake me.",
        ],
    ]

    private static func localized(_ key: String) -> String {
        let lang = Locale.preferredLanguages.first.flatMap { $0.split(separator: "-").first }
            .map(String.init)?.lowercased() ?? "pl"
        return strings[lang]?[key] ?? strings["en"]?[key] ?? key
    }
}
