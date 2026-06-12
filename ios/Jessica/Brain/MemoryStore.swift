import Foundation

/// Persistent store for Jessica's long-term memory: free-form facts the
/// user wants her to remember, and reminders with a due date.
///
/// **Backing:** a single JSON file under
/// `Application Support/Jessica/memory.json`. Writes are full-file
/// rewrites — adequate for the prototype's expected scale (<1000
/// facts, <100 active reminders) and avoids a SQLite/Core Data
/// dependency in M1.
///
/// **Concurrency:** actor-isolated. UI code awaits the actor; the
/// actor never blocks the main thread on disk I/O.
actor MemoryStore {

    // MARK: - Models

    struct Fact: Codable, Identifiable, Sendable, Equatable {
        let id: UUID
        let key: String        // normalised lowercase lookup key
        let body: String       // raw remembered phrase
        let language: String   // "pl" or "en"
        let createdAt: Date
    }

    struct Reminder: Codable, Identifiable, Sendable, Equatable {
        let id: UUID
        let body: String
        let dueAt: Date
        let language: String
        let createdAt: Date
    }

    private struct Snapshot: Codable {
        var facts: [Fact] = []
        var reminders: [Reminder] = []
    }

    // MARK: - State

    private let fileURL: URL
    private var snapshot: Snapshot

    init() {
        let fm = FileManager.default
        let baseURL = (try? fm.url(
            for: .applicationSupportDirectory,
            in: .userDomainMask,
            appropriateFor: nil,
            create: true
        )) ?? URL(fileURLWithPath: NSTemporaryDirectory())

        let dir = baseURL.appendingPathComponent("Jessica", isDirectory: true)
        try? fm.createDirectory(at: dir, withIntermediateDirectories: true)
        self.fileURL = dir.appendingPathComponent("memory.json")

        if let data = try? Data(contentsOf: fileURL),
           let loaded = try? JSONDecoder().decode(Snapshot.self, from: data) {
            self.snapshot = loaded
        } else {
            self.snapshot = Snapshot()
        }
    }

    // MARK: - Facts

    /// Stores a free-form fact. `body` is the raw user utterance after
    /// the "remember that…" prefix has been stripped. `key` is the
    /// normalised lookup string used by ``recall(matching:)``.
    @discardableResult
    func remember(body: String, language: String) -> Fact {
        let key = Self.normalise(body)
        let fact = Fact(
            id: UUID(),
            key: key,
            body: body,
            language: language,
            createdAt: Date()
        )
        snapshot.facts.append(fact)
        persist()
        return fact
    }

    /// Substring / word-overlap search across stored facts. Returns the
    /// most-recent matches first.
    func recall(matching query: String) -> [Fact] {
        let normalisedQuery = Self.normalise(query)
        let tokens = normalisedQuery
            .split(separator: " ")
            .map(String.init)
            .filter { !$0.isEmpty }
        guard !tokens.isEmpty else { return [] }

        return snapshot.facts
            .filter { fact in
                tokens.contains { fact.key.contains($0) }
            }
            .sorted { $0.createdAt > $1.createdAt }
    }

    func allFacts() -> [Fact] {
        snapshot.facts.sorted { $0.createdAt > $1.createdAt }
    }

    func forgetFact(id: UUID) {
        snapshot.facts.removeAll { $0.id == id }
        persist()
    }

    func clearFacts() {
        snapshot.facts.removeAll()
        persist()
    }

    // MARK: - Reminders

    @discardableResult
    func remind(body: String, at dueAt: Date, language: String) -> Reminder {
        let reminder = Reminder(
            id: UUID(),
            body: body,
            dueAt: dueAt,
            language: language,
            createdAt: Date()
        )
        snapshot.reminders.append(reminder)
        persist()
        return reminder
    }

    func allReminders() -> [Reminder] {
        snapshot.reminders
            .sorted { $0.dueAt < $1.dueAt }
    }

    /// Reminders whose due date is in the future.
    func upcomingReminders(now: Date = Date()) -> [Reminder] {
        snapshot.reminders
            .filter { $0.dueAt >= now }
            .sorted { $0.dueAt < $1.dueAt }
    }

    func cancelReminder(id: UUID) {
        snapshot.reminders.removeAll { $0.id == id }
        persist()
    }

    func clearReminders() {
        snapshot.reminders.removeAll()
        persist()
    }

    // MARK: - Persistence

    private func persist() {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        encoder.dateEncodingStrategy = .iso8601
        guard let data = try? encoder.encode(snapshot) else { return }
        try? data.write(to: fileURL, options: .atomic)
    }

    // MARK: - Helpers

    private static func normalise(_ text: String) -> String {
        text
            .lowercased()
            .folding(options: .diacriticInsensitive, locale: Locale(identifier: "pl_PL"))
            .replacingOccurrences(of: "[^\\p{L}\\p{N}\\s]",
                                  with: " ",
                                  options: .regularExpression)
            .split(separator: " ")
            .joined(separator: " ")
    }
}
