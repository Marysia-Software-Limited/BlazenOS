import AVFoundation

/// Listens for the "hey Jessica" / "hej Jessico" wake phrase.
///
/// **M0:** energy-threshold placeholder — sustained RMS above the
///         threshold window triggers the event.
/// **M1:** route 80 ms audio windows through the openWakeWord CoreML
///         model running on the Neural Engine (~10 ms/window on A18 Pro).
actor WakeWordDetector {
    enum Event: Sendable { case triggered }

    nonisolated let events: AsyncStream<Event>
    private nonisolated let outbox: AsyncStream<Event>.Continuation

    private let engine = AVAudioEngine()

    init() {
        (events, outbox) = AsyncStream.makeStream(of: Event.self)
    }

    func start() throws {
        let node = engine.inputNode
        let format = node.outputFormat(forBus: 0)
        let out = outbox
        let state = EnergyWindow()

        node.installTap(onBus: 0, bufferSize: 4096, format: format) { buffer, _ in
            guard let ch = buffer.floatChannelData?[0] else { return }
            let n = Int(buffer.frameLength)
            let sumSq = (0..<n).reduce(Float(0)) { $0 + ch[$1] * ch[$1] }
            let rms = sqrtf(sumSq / Float(max(n, 1)))
            state.push(rms: rms, fire: { out.yield(.triggered) })
        }
        try engine.start()
    }

    func stop() {
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        outbox.finish()
    }
}

// Mutable energy window for the AVAudioEngine tap (audio thread).
private final class EnergyWindow: @unchecked Sendable {
    private let lock = NSLock()
    private var window: [Float] = []
    private static let size = 8
    private static let minActive = 6
    private static let threshold: Float = 0.018

    func push(rms: Float, fire: () -> Void) {
        var shouldFire = false
        lock.lock()
        window.append(rms)
        if window.count > Self.size { window.removeFirst() }
        if window.count == Self.size,
           window.filter({ $0 > Self.threshold }).count >= Self.minActive {
            window.removeAll()
            shouldFire = true
        }
        lock.unlock()
        if shouldFire { fire() }
    }
}
