import SwiftUI
import JessicaCore

@main
struct JessicaApp: App {
    @StateObject private var host = CoreHost()

    var body: some Scene {
        WindowGroup {
            HomeView()
                .environmentObject(host)
                .environment(host.pipeline)
        }
    }
}

/// Erases `JessicaCore` into an `ObservableObject` so SwiftUI's
/// `@EnvironmentObject` propagation works without dragging the core
/// surface into UI code. Lifecycle is per-app, not per-scene.
///
/// Also owns the singleton ``VoicePipeline``, ``MemoryStore``,
/// ``ReminderScheduler``, and ``GeminiClient`` so their state survives
/// view restarts (rotation, scene re-entry). Mirrors Android's
/// `JessicaApp.orchestrator` ownership.
@MainActor
final class CoreHost: ObservableObject {
    let core: JessicaCore = .init()
    let memory: MemoryStore
    let reminders: ReminderScheduler
    let gemini: GeminiClient
    let pipeline: VoicePipeline

    init() {
        let memory = MemoryStore()
        let reminders = ReminderScheduler()
        let gemini = GeminiClient()
        self.memory = memory
        self.reminders = reminders
        self.gemini = gemini
        self.pipeline = VoicePipeline(
            core: core,
            memory: memory,
            reminders: reminders,
            gemini: gemini
        )

        if let url = Bundle.main.url(forResource: "intents-system", withExtension: "yaml"),
           let yaml = try? String(contentsOf: url, encoding: .utf8) {
            _ = core.loadIntents(yaml)
        }
    }
}
