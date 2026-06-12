import SwiftUI
import AVFoundation
import JessicaCore

/// Home screen of the Jessica iOS app.
///
/// Visual contract (mirrors `android/app/.../ui/HomeScreen.kt`):
///   - Header: greeting + intent-count badge.
///   - State chip describing what Jessica is doing right now.
///   - Big mic button. Tap to start/stop the always-listening pipeline.
///   - Last-turn panel ("You: …" / "Jessica: …") below the button.
///   - Language toggle at the bottom (PL / EN / Auto).
///
/// Falls back to ``PermissionDeniedView`` when the user has rejected
/// microphone access — same shape as the Android `PermissionGate`.
struct HomeView: View {
    @EnvironmentObject private var host: CoreHost
    @Environment(VoicePipeline.self) private var pipeline

    @State private var micStatus: AVAuthorizationStatus =
        AVCaptureDevice.authorizationStatus(for: .audio)
    @State private var showSettings = false

    var body: some View {
        NavigationStack {
            Group {
                if micStatus == .denied || micStatus == .restricted {
                    PermissionDeniedView()
                } else {
                    pipelineSurface
                }
            }
            .navigationTitle("Jessica")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        showSettings = true
                    } label: {
                        Image(systemName: "gearshape")
                    }
                    .accessibilityLabel(L10n.settingsTitle)
                }
            }
            .sheet(isPresented: $showSettings) {
                SettingsView()
                    .environmentObject(host)
            }
        }
        .onAppear { refreshMicStatus() }
    }

    // MARK: - Pipeline surface

    private var pipelineSurface: some View {
        VStack(spacing: 20) {
            Spacer(minLength: 16)

            stateChip(for: pipeline.state)
                .animation(.spring, value: pipeline.state)

            Text(L10n.homeGreeting)
                .font(.title2.bold())

            Text(L10n.homeStatusIntents(host.core.intentCount()))
                .font(.body)
                .foregroundStyle(.secondary)

            if pipeline.state == .recognizing, !pipeline.transcript.isEmpty {
                Text(pipeline.transcript)
                    .font(.callout)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal)
            }

            Spacer()

            micButton

            lastTurnPanel

            Spacer(minLength: 8)

            LanguageToggle(
                language: pipeline.language,
                isPinned: pipeline.isLanguagePinned,
                onPin: { pipeline.pinLanguage($0) },
                onUnpin: { pipeline.unpinLanguage() }
            )
            .padding(.bottom, 12)
        }
        .padding(.vertical, 16)
    }

    // MARK: - Mic button

    @ViewBuilder
    private var micButton: some View {
        Button {
            tapped()
        } label: {
            Label(
                buttonLabel,
                systemImage: pipeline.state == .idle ? "mic" : "mic.slash"
            )
            .font(.headline)
            .frame(maxWidth: .infinity)
            .padding()
            .background(.tint.opacity(0.12))
            .clipShape(Capsule())
        }
        .padding(.horizontal, 24)
    }

    private var buttonLabel: String {
        switch pipeline.state {
        case .idle: L10n.voiceStart
        case .speaking: L10n.voiceInterrupt
        default: L10n.voiceStop
        }
    }

    private func tapped() {
        switch pipeline.state {
        case .idle:
            refreshMicStatus()
            if micStatus == .denied || micStatus == .restricted { return }
            pipeline.start()
        case .speaking:
            pipeline.interrupt()
        default:
            pipeline.stop()
        }
    }

    private func refreshMicStatus() {
        micStatus = AVCaptureDevice.authorizationStatus(for: .audio)
    }

    // MARK: - Last-turn panel

    @ViewBuilder
    private var lastTurnPanel: some View {
        if let turn = pipeline.lastTurn {
            VStack(spacing: 4) {
                Text(String(format: L10n.lastTurnYou, turn.transcript))
                    .font(.callout)
                Text(String(format: L10n.lastTurnJessica, turn.reply))
                    .font(.callout.italic())
                    .foregroundStyle(.tint)
            }
            .multilineTextAlignment(.center)
            .padding(.horizontal, 24)
        } else {
            Text(L10n.homeListenHint)
                .font(.callout)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 24)
        }
    }

    // MARK: - State chip

    @ViewBuilder
    private func stateChip(for s: VoicePipeline.State) -> some View {
        let (label, color): (String, Color) = switch s {
        case .idle:        (L10n.voiceIdle,        .secondary)
        case .listening:   (L10n.voiceListening,   .green)
        case .recognizing: (L10n.voiceRecognizing, .orange)
        case .responding:  (L10n.voiceResponding,  .blue)
        case .speaking:    (L10n.voiceSpeaking,    .purple)
        case .error:       (L10n.voiceError,       .red)
        }
        Text(label)
            .font(.caption.weight(.semibold))
            .padding(.horizontal, 12)
            .padding(.vertical, 6)
            .background(color.opacity(0.15))
            .foregroundStyle(color)
            .clipShape(Capsule())
    }
}

// MARK: - Language toggle

private struct LanguageToggle: View {
    let language: String
    let isPinned: Bool
    let onPin: (String) -> Void
    let onUnpin: () -> Void

    var body: some View {
        VStack(spacing: 6) {
            Text(badgeText)
                .font(.caption2)
                .foregroundStyle(.secondary)
            HStack(spacing: 8) {
                pinButton(label: L10n.languagePL, code: "pl")
                pinButton(label: L10n.languageEN, code: "en")
                Button(L10n.languageAutoButton, action: onUnpin)
                    .buttonStyle(.bordered)
            }
        }
    }

    private var badgeText: String {
        String(
            format: isPinned ? L10n.languagePinned : L10n.languageAuto,
            language.uppercased()
        )
    }

    @ViewBuilder
    private func pinButton(label: String, code: String) -> some View {
        Button(label) { onPin(code) }
            .buttonStyle(.bordered)
            .tint(isPinned && language == code ? .accentColor : .secondary)
    }
}

// MARK: - Previews

#Preview("PL idle") {
    HomeView()
        .environmentObject(previewHost)
        .environment(previewHost.pipeline)
        .environment(\.locale, .init(identifier: "pl"))
}

#Preview("EN with last turn") {
    let h = previewHost
    h.pipeline.pinLanguage("en")
    return HomeView()
        .environmentObject(h)
        .environment(h.pipeline)
        .environment(\.locale, .init(identifier: "en"))
}

@MainActor
private let previewHost: CoreHost = {
    let h = CoreHost()
    _ = h.core.loadIntents("""
    version: 1
    intents:
      - name: volume_up
        triggers:
          en: ["louder"]
          pl: ["głośniej"]
        action: mutate
    """)
    return h
}()
