import Foundation

/// Idiomatic Swift façade over the Rust mobile core.
///
/// UI code talks only to this class; ``JessicaFFI`` stays internal.
///
/// M0 fallback: when ``JessicaFFI/makeHandle()`` returns nil (no Rust
/// library available), the façade runs a tiny pure-Swift matcher so
/// the UI can exercise the API contract end-to-end on a dev mac.
public final class JessicaCore: @unchecked Sendable {

    private let lock = NSLock()
    private var handle: OpaquePointer?
    private var fallback: PureSwiftIntents?

    public init() {
        if let h = JessicaFFI.makeHandle() {
            self.handle = h
        } else {
            self.fallback = PureSwiftIntents()
        }
    }

    deinit {
        JessicaFFI.free(handle)
    }

    /// Load the YAML intent catalogue. Returns `true` on success.
    @discardableResult
    public func loadIntents(_ yaml: String) -> Bool {
        lock.lock(); defer { lock.unlock() }
        if let fallback {
            return fallback.load(yaml)
        }
        return JessicaFFI.loadIntents(handle, yaml: yaml) == JESSICA_OK
    }

    /// Match a transcript against the loaded catalogue. Returns nil when no intent fires.
    public func matchIntent(transcript: String, language: String) -> IntentMatch? {
        lock.lock(); defer { lock.unlock() }
        if let fallback {
            return fallback.match(transcript: transcript, language: language)
        }
        guard let json = JessicaFFI.matchIntent(handle, transcript: transcript, language: language),
              let data = json.data(using: .utf8) else {
            return nil
        }
        return try? JSONDecoder().decode(IntentMatch.self, from: data)
    }

    /// How many intents are currently loaded.
    public func intentCount() -> Int64 {
        lock.lock(); defer { lock.unlock() }
        if let fallback {
            return Int64(fallback.count)
        }
        return JessicaFFI.intentCount(handle)
    }
}
