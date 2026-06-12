import AVFoundation

/// Speaks text aloud using `AVSpeechSynthesizer`.
///
/// Prefers the Polish Enhanced voice (`pl-PL`) for Polish locales;
/// falls back to the system default for other locales.
final class TextToSpeech: NSObject, AVSpeechSynthesizerDelegate,
                          @unchecked Sendable {
    private let synth = AVSpeechSynthesizer()
    private let lock = NSLock()
    private var pending: CheckedContinuation<Void, Never>?

    override init() {
        super.init()
        synth.delegate = self
    }

    /// Speaks `text` and suspends until the utterance finishes.
    func speak(_ text: String, language: String = "pl-PL") async {
        await withCheckedContinuation { [weak self]
            (cont: CheckedContinuation<Void, Never>) in
            guard let self else { cont.resume(); return }
            lock.lock(); pending = cont; lock.unlock()
            let utt = AVSpeechUtterance(string: text)
            utt.voice = AVSpeechSynthesisVoice(language: language)
            utt.rate = AVSpeechUtteranceDefaultSpeechRate
            synth.speak(utt)
        }
    }

    func stopSpeaking() {
        synth.stopSpeaking(at: .immediate)
    }

    // MARK: - AVSpeechSynthesizerDelegate

    func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer,
                            didFinish utterance: AVSpeechUtterance) {
        lock.lock(); let c = pending; pending = nil; lock.unlock()
        c?.resume()
    }
}
