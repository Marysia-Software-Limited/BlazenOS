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
/// Also owns the singleton ``VoicePipeline`` so its state survives
/// view restarts (rotation, scene re-entry). Mirrors Android's
/// `JessicaApp.orchestrator` ownership.
@MainActor
final class CoreHost: ObservableObject {
    let core: JessicaCore = .init()
    let pipeline: VoicePipeline

    init() {
        self.pipeline = VoicePipeline(core: core)

        if let url = Bundle.main.url(forResource: "intents-system", withExtension: "yaml"),
           let yaml = try? String(contentsOf: url, encoding: .utf8) {
            _ = core.loadIntents(yaml)
        }
    }
}
