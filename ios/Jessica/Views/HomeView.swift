import SwiftUI
import JessicaCore

struct HomeView: View {
    @EnvironmentObject private var host: CoreHost

    var body: some View {
        VStack(spacing: 16) {
            Text(L10n.homeGreeting)
                .font(.title2)
            Text(L10n.homeStatusIntents(host.core.intentCount()))
                .font(.body)
                .foregroundStyle(.secondary)
            Text(L10n.homeListenHint)
                .font(.footnote)
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
        }
        .padding(24)
    }
}

#Preview("PL") {
    HomeView()
        .environmentObject(previewHost)
        .environment(\.locale, .init(identifier: "pl"))
}

#Preview("EN") {
    HomeView()
        .environmentObject(previewHost)
        .environment(\.locale, .init(identifier: "en"))
}

private let previewHost: CoreHost = {
    let h = CoreHost()
    _ = h.core.loadIntents("""
    version: 1
    intents:
      - name: volume_up
        triggers:
          en: ["louder"]
          pl: ["g(ł|l)o(ś|s)niej"]
        action: mutate
    """)
    return h
}()
