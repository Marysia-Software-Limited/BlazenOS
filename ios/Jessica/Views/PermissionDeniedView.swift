import SwiftUI

/// Shown when the user has denied microphone access. Without the mic
/// Jessica is mute; the only recovery path is the system Settings app.
///
/// Mirrors `android/app/.../ui/PermissionGate.kt`'s denied state.
struct PermissionDeniedView: View {
    var body: some View {
        VStack(spacing: 16) {
            Spacer()
            Image(systemName: "mic.slash.fill")
                .font(.system(size: 56))
                .foregroundStyle(.secondary)
            Text(L10n.permissionRationaleTitle)
                .font(.title2.bold())
                .multilineTextAlignment(.center)
            Text(L10n.permissionRationaleBody)
                .font(.body)
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
                .padding(.horizontal, 24)
            Spacer()
            Button {
                openSettings()
            } label: {
                Label(L10n.permissionOpenSettings, systemImage: "gearshape.fill")
                    .font(.headline)
                    .frame(maxWidth: .infinity)
                    .padding()
                    .background(.tint.opacity(0.15))
                    .clipShape(Capsule())
            }
            .padding(.horizontal, 24)
            .padding(.bottom, 16)
        }
        .padding(.vertical, 24)
    }

    private func openSettings() {
        guard let url = URL(string: UIApplication.openSettingsURLString) else { return }
        UIApplication.shared.open(url)
    }
}

#Preview("PL") {
    PermissionDeniedView()
        .environment(\.locale, .init(identifier: "pl"))
}

#Preview("EN") {
    PermissionDeniedView()
        .environment(\.locale, .init(identifier: "en"))
}
