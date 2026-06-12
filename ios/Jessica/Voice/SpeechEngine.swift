import Speech
import AVFoundation

/// Transcribes a single utterance from the microphone using
/// `SpeechAnalyzer` + `SpeechTranscriber` (iOS 26).
///
/// Audio capture uses `AVAudioEngine` with `AnalyzerInputConverter`.
/// On iOS 27+ you can swap the capture layer for the simpler
/// `CaptureInputSequenceProvider.providerWithSession(from:compatibleWith:)`.
actor SpeechEngine {

    func requestPermission() async -> Bool {
        await AVCaptureDevice.requestAccess(for: .audio)
    }

    /// Records from the microphone and returns the best final transcription.
    ///
    /// Captures for up to `maxDuration` seconds, then finalises analysis.
    func transcribeUtterance(
        locale: Locale = .current,
        maxDuration: Duration = .seconds(8)
    ) async throws -> String {
        guard let supported = SpeechTranscriber.supportedLocale(equivalentTo: locale)
                           ?? SpeechTranscriber.supportedLocale(
                               equivalentTo: Locale(identifier: "pl-PL"))
        else { throw SpeechError.localeUnsupported }

        let transcriber = SpeechTranscriber(locale: supported,
                                            preset: .progressiveTranscription)

        if let req = try await AssetInventory.assetInstallationRequest(
            supporting: [transcriber]) {
            try await req.downloadAndInstall()
        }

        // Determine the audio format the analyzer needs.
        let analyzerFormat = await SpeechAnalyzer.bestAvailableAudioFormat(
            compatibleWith: [transcriber])
        let converter = AnalyzerInputConverter(analyzerFormat: analyzerFormat)

        // Build the input sequence fed via AVAudioEngine tap.
        let (inputSeq, inputBuilder) = AsyncStream.makeStream(of: AnalyzerInput.self)

        let audioEngine = AVAudioEngine()
        let inputNode = audioEngine.inputNode
        let recordingFormat = inputNode.outputFormat(forBus: 0)

        inputNode.installTap(onBus: 0, bufferSize: 4096,
                             format: recordingFormat) { buffer, time in
            if let inputs = try? converter.convert(buffer, at: time) {
                for input in inputs { inputBuilder.yield(input) }
            }
        }
        try audioEngine.start()

        let analyzer = SpeechAnalyzer(modules: [transcriber])

        // Buffer partial results while the analysis runs.
        let (partials, outbox) = AsyncStream.makeStream(of: String.self)
        let resultTask = Task {
            do {
                for try await result in transcriber.results {
                    outbox.yield(String(result.text.characters))
                }
            } catch { }
            outbox.finish()
        }

        // Stop capture after maxDuration.
        let timeoutTask = Task {
            try? await Task.sleep(for: maxDuration)
            inputNode.removeTap(onBus: 0)
            audioEngine.stop()
            if let flushed = try? converter.flush() {
                for input in flushed { inputBuilder.yield(input) }
            }
            inputBuilder.finish()
        }

        // Drive the analysis until the input sequence finishes.
        let lastTime = try await analyzer.analyzeSequence(inputSeq)
        timeoutTask.cancel()

        // Ensure capture has stopped.
        if audioEngine.isRunning {
            inputNode.removeTap(onBus: 0)
            audioEngine.stop()
            if let flushed = try? converter.flush() {
                for input in flushed { inputBuilder.yield(input) }
            }
            inputBuilder.finish()
        }

        if let last = lastTime {
            try await analyzer.finalizeAndFinish(through: last)
        } else {
            try analyzer.cancelAndFinishNow()
        }

        await resultTask.value

        var latest = ""
        for await partial in partials { latest = partial }
        return latest
    }

    enum SpeechError: Error, LocalizedError {
        case localeUnsupported
        case microphoneUnavailable

        var errorDescription: String? {
            switch self {
            case .localeUnsupported:    "Speech locale not supported on this device."
            case .microphoneUnavailable: "Microphone not available."
            }
        }
    }
}
