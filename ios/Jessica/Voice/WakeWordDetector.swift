import AVFoundation
import Speech

/// Listens continuously for the "hey Jessica" / "hej Jessico" wake
/// phrase using iOS 26's `SpeechAnalyzer` + `SpeechTranscriber`.
///
/// **M1:** scans partial transcripts for the wake phrase token. Battery
///         cost is non-trivial but acceptable for foreground prototype
///         use. Debounced so we yield at most one event per
///         ``cooldown`` window.
/// **M2:** swap for openWakeWord ONNX (~10 ms/window on A17 Pro
///         Neural Engine) so background-mode listening is feasible.
///
/// While the host has captured the post-wake utterance via
/// ``SpeechEngine``, the detector should be stopped (``stop``) to free
/// the microphone — then started again once Jessica has finished
/// speaking. ``VoicePipeline`` owns that lifecycle.
actor WakeWordDetector {
    enum Event: Sendable { case triggered }

    enum WakeError: Error, LocalizedError {
        case localeUnsupported
        case audioFormatUnavailable

        var errorDescription: String? {
            switch self {
            case .localeUnsupported:    "Wake-word locale not supported."
            case .audioFormatUnavailable: "No compatible audio format."
            }
        }
    }

    nonisolated let events: AsyncStream<Event>
    private nonisolated let outbox: AsyncStream<Event>.Continuation

    private let cooldown: TimeInterval = 4
    private var lastTriggerAt: Date = .distantPast

    private var transcriber: SpeechTranscriber?
    private var analyzer: SpeechAnalyzer?
    private var engine: AVAudioEngine?
    private var inputBuilder: AsyncStream<AnalyzerInput>.Continuation?
    private var listenerTask: Task<Void, Never>?
    private var analyzerTask: Task<Void, Never>?

    init() {
        (events, outbox) = AsyncStream.makeStream(of: Event.self)
    }

    func start() async throws {
        // Idempotent — if already listening, do nothing.
        guard analyzer == nil else { return }

        let preferredLocale = await SpeechTranscriber.supportedLocale(
            equivalentTo: Locale(identifier: "pl-PL")
        )
        let locale: Locale
        if let preferredLocale {
            locale = preferredLocale
        } else if let englishLocale = await SpeechTranscriber.supportedLocale(
            equivalentTo: Locale(identifier: "en-US")
        ) {
            locale = englishLocale
        } else {
            throw WakeError.localeUnsupported
        }

        let transcriber = SpeechTranscriber(
            locale: locale,
            preset: .progressiveTranscription
        )

        if let req = try await AssetInventory.assetInstallationRequest(
            supporting: [transcriber]
        ) {
            try await req.downloadAndInstall()
        }

        guard let analyzerFormat = await SpeechAnalyzer.bestAvailableAudioFormat(
            compatibleWith: [transcriber]
        ) else { throw WakeError.audioFormatUnavailable }
        let converter = AnalyzerInputConverter(analyzerFormat: analyzerFormat)

        let (inputSeq, inputBuilder) = AsyncStream.makeStream(of: AnalyzerInput.self)

        let engine = AVAudioEngine()
        let inputNode = engine.inputNode
        let recordingFormat = inputNode.outputFormat(forBus: 0)
        inputNode.installTap(
            onBus: 0,
            bufferSize: 4096,
            format: recordingFormat
        ) { buffer, time in
            if let inputs = try? converter.convert(buffer, at: time) {
                for input in inputs { inputBuilder.yield(input) }
            }
        }
        try engine.start()

        self.transcriber = transcriber
        self.engine = engine
        self.inputBuilder = inputBuilder

        let out = outbox
        let listener = Task { [weak self] in
            do {
                for try await result in transcriber.results {
                    let text = String(result.text.characters)
                    if await self?.shouldFire(for: text) == true {
                        out.yield(.triggered)
                    }
                }
            } catch {
                // SpeechTranscriber stream ended — common on stop().
            }
        }
        self.listenerTask = listener

        let analyzer = SpeechAnalyzer(modules: [transcriber])
        self.analyzer = analyzer

        // Drive the analysis off-actor so `start()` can return and the
        // pipeline can move on. The task lives until ``stop`` cancels
        // the input sequence.
        analyzerTask = Task {
            _ = try? await analyzer.analyzeSequence(inputSeq)
        }
    }

    func stop() async {
        listenerTask?.cancel()
        analyzerTask?.cancel()
        listenerTask = nil
        analyzerTask = nil

        if let engine {
            engine.inputNode.removeTap(onBus: 0)
            engine.stop()
        }
        engine = nil

        inputBuilder?.finish()
        inputBuilder = nil

        if let analyzer {
            await analyzer.cancelAndFinishNow()
        }
        analyzer = nil
        transcriber = nil
    }

    // MARK: - Phrase matching

    private func shouldFire(for text: String) -> Bool {
        guard Self.containsWakePhrase(text) else { return false }
        let now = Date()
        guard now.timeIntervalSince(lastTriggerAt) > cooldown else { return false }
        lastTriggerAt = now
        return true
    }

    /// Case- and diacritic-insensitive lookup. Matches "jessica",
    /// "jessico", "jessika", "jess" — handles common Polish-by-ear
    /// mistranscriptions.
    private static func containsWakePhrase(_ text: String) -> Bool {
        let needle = text
            .lowercased()
            .folding(options: .diacriticInsensitive, locale: Locale(identifier: "pl_PL"))
        for token in wakeTokens where needle.contains(token) {
            return true
        }
        return false
    }

    private static let wakeTokens: [String] = [
        "jessica",
        "jessico",
        "jessika",
        "dzesika",   // PL-by-ear spelling, after diacritic fold
        "dzesiko",
    ]
}
