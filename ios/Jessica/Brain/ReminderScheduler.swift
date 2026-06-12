import Foundation
import UserNotifications

/// Wraps `UNUserNotificationCenter` for Jessica's reminders.
///
/// Authorisation is requested lazily on the first scheduling attempt.
/// Reminders are scheduled as `UNCalendarNotificationTrigger`s keyed
/// by the ``MemoryStore/Reminder/id`` UUID, so cancelling is just a
/// matter of removing pending requests with that identifier.
///
/// Also extracts a due date from a freeform user phrase via
/// ``NSDataDetector`` — works in PL+EN for common patterns ("jutro o
/// 10:00", "za 30 minut", "in 2 hours", etc.). When no date is found
/// the caller is expected to fall back to a sensible default or ask
/// the user to repeat.
actor ReminderScheduler {

    enum ScheduleError: Error {
        case notAuthorised
        case schedulingFailed(underlying: Error?)
    }

    private let center = UNUserNotificationCenter.current()
    private var authorised: Bool = false

    /// Idempotent permission request. Returns true once the user has
    /// authorised (`.authorized` or `.provisional`).
    @discardableResult
    func requestAuthorisationIfNeeded() async -> Bool {
        if authorised { return true }
        let settings = await center.notificationSettings()
        switch settings.authorizationStatus {
        case .authorized, .provisional, .ephemeral:
            authorised = true
            return true
        case .denied:
            return false
        case .notDetermined:
            do {
                authorised = try await center.requestAuthorization(
                    options: [.alert, .sound, .badge]
                )
                return authorised
            } catch {
                return false
            }
        @unknown default:
            return false
        }
    }

    /// Schedules a `UNCalendarNotificationTrigger` for the reminder.
    /// Returns the scheduled date (which may differ from `reminder.dueAt`
    /// by sub-minute amounts because the trigger fires on calendar
    /// boundaries).
    func schedule(_ reminder: MemoryStore.Reminder) async throws {
        guard await requestAuthorisationIfNeeded() else {
            throw ScheduleError.notAuthorised
        }

        let content = UNMutableNotificationContent()
        content.title = "Jessica"
        content.body = reminder.body
        content.sound = .default

        let components = Calendar.current.dateComponents(
            [.year, .month, .day, .hour, .minute],
            from: reminder.dueAt
        )
        let trigger = UNCalendarNotificationTrigger(
            dateMatching: components,
            repeats: false
        )

        let request = UNNotificationRequest(
            identifier: reminder.id.uuidString,
            content: content,
            trigger: trigger
        )

        do {
            try await center.add(request)
        } catch {
            throw ScheduleError.schedulingFailed(underlying: error)
        }
    }

    func cancel(reminderId: UUID) {
        center.removePendingNotificationRequests(
            withIdentifiers: [reminderId.uuidString]
        )
    }

    func cancelAll() {
        center.removeAllPendingNotificationRequests()
    }

    // MARK: - Date extraction

    /// Best-effort due-date extraction. Returns `nil` when neither
    /// `NSDataDetector` nor the basic Polish-pattern fallback can
    /// identify a date.
    static func extractDueDate(from text: String, now: Date = Date()) -> Date? {
        if let detected = detectViaNSDataDetector(text: text, now: now) {
            return detected
        }
        return detectPolishFallback(text: text, now: now)
    }

    private static func detectViaNSDataDetector(text: String, now: Date) -> Date? {
        guard let detector = try? NSDataDetector(
            types: NSTextCheckingResult.CheckingType.date.rawValue
        ) else { return nil }
        let range = NSRange(text.startIndex..., in: text)
        let matches = detector.matches(in: text, options: [], range: range)
        let candidate = matches.compactMap { $0.date }.first { $0 > now }
        return candidate
    }

    /// Tiny PL fallback for common relative patterns that the
    /// `NSDataDetector` misses. Order matters — longest match wins.
    private static func detectPolishFallback(text: String, now: Date) -> Date? {
        let lowered = text.lowercased()
        if let m = match(#"za\s+(\d+)\s+godzin"#, in: lowered),
           let hours = Int(m) {
            return Calendar.current.date(byAdding: .hour, value: hours, to: now)
        }
        if let m = match(#"za\s+(\d+)\s+minut"#, in: lowered),
           let minutes = Int(m) {
            return Calendar.current.date(byAdding: .minute, value: minutes, to: now)
        }
        if lowered.contains("jutro") {
            return Calendar.current.date(byAdding: .day, value: 1, to: now)
                .flatMap { setHour(9, minute: 0, on: $0) }
        }
        if lowered.contains("pojutrze") {
            return Calendar.current.date(byAdding: .day, value: 2, to: now)
                .flatMap { setHour(9, minute: 0, on: $0) }
        }
        return nil
    }

    private static func match(_ pattern: String, in text: String) -> String? {
        guard let regex = try? NSRegularExpression(pattern: pattern) else { return nil }
        let range = NSRange(text.startIndex..., in: text)
        guard let result = regex.firstMatch(in: text, options: [], range: range),
              result.numberOfRanges >= 2,
              let captured = Range(result.range(at: 1), in: text) else { return nil }
        return String(text[captured])
    }

    private static func setHour(_ hour: Int, minute: Int, on day: Date) -> Date? {
        var components = Calendar.current.dateComponents(
            [.year, .month, .day],
            from: day
        )
        components.hour = hour
        components.minute = minute
        return Calendar.current.date(from: components)
    }
}
