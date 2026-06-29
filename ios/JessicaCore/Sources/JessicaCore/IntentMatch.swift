import Foundation

/// One match result returned by ``JessicaCore/matchIntent(transcript:language:)``.
///
/// Mirrors the `IntentMatch` shape in
/// `domains/jessica-core/src/intent.rs` so a JSON round-trip
/// across the FFI is lossless. Treat this struct as part of the
/// Rust ↔ Swift contract — changes here require the matching Rust update.
public struct IntentMatch: Equatable, Sendable, Codable {
    public enum Confirm: String, Sendable, Codable {
        case never
        case soft
        case hard
    }

    public let name: String
    public let language: String
    public let action: String
    public let tool: String?
    public let params: [String: String]
    public let confirm: Confirm

    public init(
        name: String,
        language: String,
        action: String,
        tool: String? = nil,
        params: [String: String] = [:],
        confirm: Confirm = .never
    ) {
        self.name = name
        self.language = language
        self.action = action
        self.tool = tool
        self.params = params
        self.confirm = confirm
    }
}
