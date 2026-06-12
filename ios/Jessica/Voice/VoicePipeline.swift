import Observation
import SwiftUI
import JessicaCore

/// Voice-pipeline state machine for the Jessica app.
///
/// Coordinates: wake-word detection → ASR → intent match / memory /
/// Gemini / Foundation Models → TTS.  Exposed as `@Observable` so
/// SwiftUI views rebuild automatically on state transitions.
///
/// Mirrors `android/app/.../voice/JessicaOrchestrator.kt` — same state
/// names, same language-pinning rules. PL is the development default;
/// the user can pin PL or EN explicitly (UI toggle OR voice intent
/// `language_pin_pl` / `language_pin_en` / `language_unpin`).
///
/// Intent routing (M1 prototype):
/// - `volume_up`, `volume_down`, `time_query`, `what_can_you_do`,
///   `stop`, `language_*` → ``ReplyGenerator`` (canned).
/// - `remember_fact` / `recall_fact` → ``MemoryStore``.
/// - `set_reminder` / `list_reminders` / `cancel_reminders` →
///   ``MemoryStore`` + ``ReminderScheduler``.
/// - `news_query` → ``GeminiClient`` (cloud, opt-in).
/// - No match → Foundation Models (on-device LLM) → Gemini (cloud
///   fallback) → canned "I don't know" reply.
@Observable
@MainActor
final class VoicePipeline {

    enum State: Equatable {
        case idle
        case listening      // wake-word window open
        case recognizing    // capturing utterance via SpeechEngine
        case responding     // Foundation Models / Gemini thinking
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

    private(set) var language: String = "pl"
    private(set) var isLanguagePinned: Bool = false

    private let core: JessicaCore
    private let memory: MemoryStore
    private let reminders: ReminderScheduler
    private let gemini: GeminiClient

    private let wake = WakeWordDetector()
    private let asr = SpeechEngine()
    private let llm = FoundationModelResponder()
    private let tts = TextToSpeech()
    private var pipelineTask: Task<Void, Never>?

    init(
        core: JessicaCore,
        memory: MemoryStore,
        reminders: ReminderScheduler,
        gemini: GeminiClient
    ) {
        self.core = core
        self.memory = memory
        self.reminders = reminders
        self.gemini = gemini
    }

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
        await llm.prepare(knownIntents: [])

        do {
            try await wake.start()
        } catch {
            state = .error(error.localizedDescription); return
        }
        state = .listening

        for await _ in wake.events {
            guard !Task.isCancelled else { break }
            await wake.stop()        // free the mic for the utterance ASR
            await handleWake()
            guard !Task.isCancelled else { break }
            do {
                try await wake.start()
                state = .listening
            } catch {
                state = .error(error.localizedDescription); return
            }
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

        let asrLanguage = asrLocale.identifier
            .split(separator: "-").first
            .map { String($0).lowercased() }
            ?? "pl"

        await routeUtterance(text, asrLanguage: asrLanguage)
    }

    // MARK: - Intent routing

    private func routeUtterance(_ text: String, asrLanguage: String) async {
        // Strip the wake phrase if the user included it ("hej Jessico, …").
        let stripped = Self.stripWakePhrase(from: text)
        let payload = stripped.isEmpty ? text : stripped

        if let match = core.matchIntent(transcript: payload, language: asrLanguage) {
            if match.name.hasPrefix("language_") {
                applyLanguageIntent(match)
            }
            let effective = isLanguagePinned ? language : asrLanguage
            await handleMatchedIntent(match, transcript: payload, language: effective)
            return
        }

        // No structural intent matched — defer to the LLM for free-form
        // conversation, with Gemini as the cloud fallback.
        let effective = isLanguagePinned ? language : asrLanguage
        await handleOpenDomain(payload, language: effective)
    }

    private func handleMatchedIntent(
        _ match: IntentMatch,
        transcript: String,
        language: String
    ) async {
        switch match.name {
        case "remember_fact":
            await handleRemember(transcript: transcript, language: language)
        case "recall_fact":
            await handleRecall(transcript: transcript, language: language)
        case "set_reminder":
            await handleSetReminder(transcript: transcript, language: language)
        case "list_reminders":
            await handleListReminders(language: language)
        case "cancel_reminders":
            await handleCancelReminders(language: language)
        case "news_query":
            await handleNewsQuery(transcript: transcript, language: language)
        case "stop":
            tts.stopSpeaking()
            await complete(
                transcript: transcript,
                reply: ReplyGenerator.reply(match: match, language: language),
                language: language
            )
        default:
            await complete(
                transcript: transcript,
                reply: ReplyGenerator.reply(match: match, language: language),
                language: language
            )
        }
    }

    // MARK: - Memory handlers

    private func handleRemember(transcript: String, language: String) async {
        let body = CommandParser.body(after: rememberPrefixes(language: language), in: transcript)
        guard !body.isEmpty else {
            await complete(
                transcript: transcript,
                reply: language.hasPrefix("pl")
                    ? "Co mam zapamiętać?"
                    : "What should I remember?",
                language: language
            )
            return
        }
        await memory.remember(body: body, language: language)
        await complete(
            transcript: transcript,
            reply: language.hasPrefix("pl")
                ? "Zapamiętałam."
                : "I'll remember that.",
            language: language
        )
    }

    private func handleRecall(transcript: String, language: String) async {
        let query = CommandParser.body(after: recallPrefixes(language: language), in: transcript)
        let hits = await memory.recall(matching: query.isEmpty ? transcript : query)
        guard let top = hits.first else {
            await complete(
                transcript: transcript,
                reply: language.hasPrefix("pl")
                    ? "Nic na ten temat nie pamiętam."
                    : "I don't remember anything about that.",
                language: language
            )
            return
        }
        await complete(
            transcript: transcript,
            reply: top.body,
            language: language
        )
    }

    // MARK: - Reminder handlers

    private func handleSetReminder(transcript: String, language: String) async {
        let body = CommandParser.body(after: reminderPrefixes(language: language), in: transcript)
        let dueAt = ReminderScheduler.extractDueDate(from: transcript)
            ?? Calendar.current.date(byAdding: .minute, value: 60, to: Date())
            ?? Date().addingTimeInterval(3600)

        let cleanedBody = body.isEmpty ? transcript : body
        let reminder = await memory.remind(body: cleanedBody, at: dueAt, language: language)
        do {
            try await reminders.schedule(reminder)
        } catch {
            await complete(
                transcript: transcript,
                reply: language.hasPrefix("pl")
                    ? "Nie mogę ustawić powiadomień — sprawdź uprawnienia."
                    : "I can't schedule notifications — check Settings.",
                language: language
            )
            return
        }

        let timeFormatter = DateFormatter()
        timeFormatter.dateStyle = .medium
        timeFormatter.timeStyle = .short
        timeFormatter.locale = Locale(identifier: language.hasPrefix("pl") ? "pl_PL" : "en_US")
        let formatted = timeFormatter.string(from: dueAt)

        let reply = language.hasPrefix("pl")
            ? "Przypomnę: \(formatted)."
            : "I'll remind you: \(formatted)."
        await complete(transcript: transcript, reply: reply, language: language)
    }

    private func handleListReminders(language: String) async {
        let upcoming = await memory.upcomingReminders()
        guard !upcoming.isEmpty else {
            await complete(
                transcript: transcript,
                reply: language.hasPrefix("pl")
                    ? "Nie masz nadchodzących przypomnień."
                    : "You have no upcoming reminders.",
                language: language
            )
            return
        }

        let timeFormatter = DateFormatter()
        timeFormatter.dateStyle = .none
        timeFormatter.timeStyle = .short
        timeFormatter.locale = Locale(identifier: language.hasPrefix("pl") ? "pl_PL" : "en_US")

        let preview = upcoming.prefix(3).map { reminder in
            let when = timeFormatter.string(from: reminder.dueAt)
            return language.hasPrefix("pl")
                ? "\(when) — \(reminder.body)"
                : "\(when) — \(reminder.body)"
        }.joined(separator: "; ")

        await complete(transcript: transcript, reply: preview, language: language)
    }

    private func handleCancelReminders(language: String) async {
        let all = await memory.allReminders()
        for r in all { await reminders.cancel(reminderId: r.id) }
        await memory.clearReminders()
        await complete(
            transcript: transcript,
            reply: language.hasPrefix("pl")
                ? "Wszystkie przypomnienia anulowane."
                : "All reminders cancelled.",
            language: language
        )
    }

    // MARK: - News / open domain

    private func handleNewsQuery(transcript: String, language: String) async {
        state = .responding
        let prompt = language.hasPrefix("pl")
            ? "Streść w 1-2 zdaniach najnowsze wiadomości z Polski lub świata. Wymień nazwy miejsc i osób."
            : "In 1-2 sentences, summarise today's top news. Name places and people."
        await callGemini(prompt: prompt, transcript: transcript, language: language)
    }

    private func handleOpenDomain(_ text: String, language: String) async {
        state = .responding
        if let result = try? await llm.respond(to: text), !result.reply.isEmpty {
            await complete(transcript: text, reply: result.reply, language: language)
            return
        }
        // Foundation Models unavailable or empty → cloud fallback.
        await callGemini(prompt: text, transcript: text, language: language)
    }

    private func callGemini(prompt: String, transcript: String, language: String) async {
        do {
            let reply = try await gemini.answer(prompt: prompt, language: language)
            await complete(transcript: transcript, reply: reply, language: language)
        } catch GeminiClient.GeminiError.missingAPIKey {
            await complete(
                transcript: transcript,
                reply: language.hasPrefix("pl")
                    ? "Najpierw dodaj klucz Gemini w ustawieniach."
                    : "Add a Gemini API key in Settings first.",
                language: language
            )
        } catch {
            await complete(
                transcript: transcript,
                reply: ReplyGenerator.reply(match: nil, language: language),
                language: language
            )
        }
    }

    // MARK: - Internals

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

    private func rememberPrefixes(language: String) -> [String] {
        language.hasPrefix("pl")
            ? ["zapamiętaj że", "zapamiętaj, że", "pamiętaj że", "pamiętaj, że"]
            : ["remember that", "remember"]
    }

    private func recallPrefixes(language: String) -> [String] {
        language.hasPrefix("pl")
            ? ["co wiesz o", "co pamiętasz o", "co wiesz na temat"]
            : ["what do you know about", "what did i tell you about", "tell me about"]
    }

    private func reminderPrefixes(language: String) -> [String] {
        language.hasPrefix("pl")
            ? ["przypomnij mi że", "przypomnij mi, że", "przypomnij mi"]
            : ["remind me to", "remind me that", "remind me"]
    }

    /// Strip "hey Jessica" / "hej jessico" preambles from the very start
    /// of the recognised utterance so the intent matcher sees the actual
    /// command.
    private static func stripWakePhrase(from text: String) -> String {
        let pattern = #"^\s*(hey|hej|hi|hello|cześć)\s+(jess(ica|ico|ika)|d[zż]es(ika|iko))\s*[,.:;]?\s*"#
        guard let regex = try? NSRegularExpression(
            pattern: pattern,
            options: [.caseInsensitive]
        ) else { return text }
        let range = NSRange(text.startIndex..., in: text)
        let stripped = regex.stringByReplacingMatches(
            in: text,
            options: [],
            range: range,
            withTemplate: ""
        )
        return stripped.trimmingCharacters(in: .whitespaces)
    }
}

// MARK: - CommandParser

/// Extracts trailing body text after one of a set of trigger prefixes.
/// Used to pull "zapamiętaj że [BODY]" → "BODY" out of the transcript.
private enum CommandParser {
    static func body(after prefixes: [String], in text: String) -> String {
        let lower = text.lowercased()
        for prefix in prefixes {
            if let range = lower.range(of: prefix) {
                let after = text.index(text.startIndex, offsetBy: lower.distance(from: lower.startIndex, to: range.upperBound))
                let tail = text[after...]
                return tail.trimmingCharacters(in: .whitespacesAndNewlines)
            }
        }
        return ""
    }
}
