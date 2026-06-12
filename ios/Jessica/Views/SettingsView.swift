import SwiftUI

/// Settings sheet: Gemini API key entry, memory inspector, reminder
/// list, clear-memory.
///
/// Form-style layout. Sheet-presented from ``HomeView``'s toolbar
/// gear so the voice loop keeps running underneath.
struct SettingsView: View {
    @EnvironmentObject private var host: CoreHost
    @Environment(\.dismiss) private var dismiss

    @State private var apiKey: String = ""
    @State private var savedAPIKey: Bool = false
    @State private var facts: [MemoryStore.Fact] = []
    @State private var reminders: [MemoryStore.Reminder] = []
    @State private var showClearFactsConfirm = false
    @State private var showClearRemindersConfirm = false

    var body: some View {
        NavigationStack {
            Form {
                geminiSection
                memorySection
                remindersSection
                aboutSection
            }
            .navigationTitle(L10n.settingsTitle)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button(L10n.settingsDone) { dismiss() }
                }
            }
            .task { await refresh() }
        }
    }

    // MARK: - Gemini

    private var geminiSection: some View {
        Section {
            if savedAPIKey {
                Label(L10n.settingsGeminiSaved, systemImage: "checkmark.seal.fill")
                    .foregroundStyle(.green)
            }
            SecureField(L10n.settingsGeminiPlaceholder, text: $apiKey)
            HStack {
                Button(L10n.settingsGeminiSave) {
                    KeychainStore.write(.geminiAPIKey, value: apiKey)
                    apiKey = ""
                    savedAPIKey = KeychainStore.read(.geminiAPIKey)?.isEmpty == false
                }
                .disabled(apiKey.isEmpty)
                Spacer()
                Button(role: .destructive) {
                    KeychainStore.delete(.geminiAPIKey)
                    savedAPIKey = false
                } label: {
                    Text(L10n.settingsGeminiClear)
                }
                .disabled(!savedAPIKey)
            }
        } header: {
            Text(L10n.settingsGeminiSection)
        } footer: {
            Text(L10n.settingsGeminiFooter)
        }
    }

    // MARK: - Memory

    private var memorySection: some View {
        Section {
            if facts.isEmpty {
                Text(L10n.settingsMemoryEmpty)
                    .foregroundStyle(.secondary)
            } else {
                ForEach(facts) { fact in
                    VStack(alignment: .leading, spacing: 4) {
                        Text(fact.body)
                        Text(fact.createdAt.formatted(.dateTime))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                .onDelete { offsets in
                    Task {
                        for index in offsets {
                            await host.memory.forgetFact(id: facts[index].id)
                        }
                        await refresh()
                    }
                }
            }
            if !facts.isEmpty {
                Button(role: .destructive) {
                    showClearFactsConfirm = true
                } label: {
                    Text(L10n.settingsMemoryClear)
                }
            }
        } header: {
            Text(L10n.settingsMemorySection)
        }
        .confirmationDialog(
            L10n.settingsMemoryConfirmClear,
            isPresented: $showClearFactsConfirm
        ) {
            Button(role: .destructive) {
                Task {
                    await host.memory.clearFacts()
                    await refresh()
                }
            } label: {
                Text(L10n.settingsMemoryClear)
            }
        }
    }

    // MARK: - Reminders

    private var remindersSection: some View {
        Section {
            if reminders.isEmpty {
                Text(L10n.settingsRemindersEmpty)
                    .foregroundStyle(.secondary)
            } else {
                ForEach(reminders) { reminder in
                    VStack(alignment: .leading, spacing: 4) {
                        Text(reminder.body)
                        Text(reminder.dueAt.formatted(.dateTime))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                .onDelete { offsets in
                    Task {
                        for index in offsets {
                            let reminder = reminders[index]
                            await host.reminders.cancel(reminderId: reminder.id)
                            await host.memory.cancelReminder(id: reminder.id)
                        }
                        await refresh()
                    }
                }
            }
            if !reminders.isEmpty {
                Button(role: .destructive) {
                    showClearRemindersConfirm = true
                } label: {
                    Text(L10n.settingsRemindersClear)
                }
            }
        } header: {
            Text(L10n.settingsRemindersSection)
        }
        .confirmationDialog(
            L10n.settingsRemindersConfirmClear,
            isPresented: $showClearRemindersConfirm
        ) {
            Button(role: .destructive) {
                Task {
                    await host.reminders.cancelAll()
                    await host.memory.clearReminders()
                    await refresh()
                }
            } label: {
                Text(L10n.settingsRemindersClear)
            }
        }
    }

    // MARK: - About

    private var aboutSection: some View {
        Section {
            LabeledContent(L10n.settingsAboutVersion, value: appVersion)
            LabeledContent(L10n.settingsAboutIntents, value: "\(host.core.intentCount())")
        } header: {
            Text(L10n.settingsAboutSection)
        }
    }

    private var appVersion: String {
        let info = Bundle.main.infoDictionary
        let short = info?["CFBundleShortVersionString"] as? String ?? "0.0.0"
        let build = info?["CFBundleVersion"] as? String ?? "0"
        return "\(short) (\(build))"
    }

    // MARK: - Refresh

    private func refresh() async {
        async let factsTask = host.memory.allFacts()
        async let remindersTask = host.memory.upcomingReminders()
        let (loadedFacts, loadedReminders) = await (factsTask, remindersTask)
        self.facts = loadedFacts
        self.reminders = loadedReminders
        self.savedAPIKey = KeychainStore.read(.geminiAPIKey)?.isEmpty == false
    }
}
