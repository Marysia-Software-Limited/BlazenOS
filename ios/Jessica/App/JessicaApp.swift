import SwiftUI
import JessicaCore

@main
struct JessicaApp: App {
    @StateObject private var core = CoreHost()

    var body: some Scene {
        WindowGroup {
            HomeView()
                .environmentObject(core)
        }
    }
}

/// Erases `JessicaCore` into an `ObservableObject` so SwiftUI's
/// `@EnvironmentObject` propagation works without dragging the core
/// surface into UI code. Lifecycle is per-app, not per-scene.
@MainActor
final class CoreHost: ObservableObject {
    let core: JessicaCore = .init()

    init() {
        guard let url = Bundle.main.url(forResource: "intents-system", withExtension: "yaml"),
              let yaml = try? String(contentsOf: url, encoding: .utf8) else {
            return
        }
        _ = core.loadIntents(yaml)
    }
}
