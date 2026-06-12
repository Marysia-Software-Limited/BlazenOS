import Foundation

/// Thin Gemini REST client. The prototype only uses
/// `generateContent` on `gemini-1.5-flash` — single-turn prompt → text
/// reply.
///
/// The API key comes from the Keychain via ``KeychainStore``. The
/// Settings screen lets the user paste/replace/clear it. If the key is
/// missing or invalid, ``answer`` throws ``GeminiError/missingAPIKey``
/// or ``apiError`` and the pipeline falls back to a canned reply.
actor GeminiClient {

    enum GeminiError: Error, LocalizedError {
        case missingAPIKey
        case transportFailure(URLError)
        case apiError(status: Int, body: String)
        case malformedResponse

        var errorDescription: String? {
            switch self {
            case .missingAPIKey:
                "No Gemini API key — open Settings to add one."
            case .transportFailure(let err):
                "Network error: \(err.localizedDescription)"
            case .apiError(let status, _):
                "Gemini returned HTTP \(status)."
            case .malformedResponse:
                "Couldn't read Gemini's reply."
            }
        }
    }

    private let session: URLSession
    private let model: String

    init(model: String = "gemini-1.5-flash") {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 20
        config.timeoutIntervalForResource = 30
        self.session = URLSession(configuration: config)
        self.model = model
    }

    /// Send a single prompt to Gemini and return the first text part of
    /// the first candidate. Language is preserved by the model — we
    /// prepend a short system-style instruction so the reply stays
    /// short enough to speak aloud.
    func answer(prompt: String, language: String) async throws -> String {
        guard let apiKey = KeychainStore.read(.geminiAPIKey), !apiKey.isEmpty else {
            throw GeminiError.missingAPIKey
        }

        let preamble = language.hasPrefix("pl")
            ? "Odpowiadaj zwięźle, jednym lub dwoma zdaniami, po polsku."
            : "Reply briefly in one or two sentences, in English."
        let fullPrompt = "\(preamble)\n\nPytanie: \(prompt)"

        let endpoint = "https://generativelanguage.googleapis.com/v1beta/models/\(model):generateContent"
        guard var components = URLComponents(string: endpoint) else {
            throw GeminiError.malformedResponse
        }
        components.queryItems = [URLQueryItem(name: "key", value: apiKey)]
        guard let url = components.url else { throw GeminiError.malformedResponse }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")

        let payload = GenerateContentRequest(
            contents: [Content(parts: [Part(text: fullPrompt)])]
        )
        request.httpBody = try JSONEncoder().encode(payload)

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch let urlError as URLError {
            throw GeminiError.transportFailure(urlError)
        }

        guard let http = response as? HTTPURLResponse else {
            throw GeminiError.malformedResponse
        }
        guard (200..<300).contains(http.statusCode) else {
            let body = String(data: data, encoding: .utf8) ?? "<binary>"
            throw GeminiError.apiError(status: http.statusCode, body: body)
        }

        let decoded = try JSONDecoder().decode(GenerateContentResponse.self, from: data)
        guard let text = decoded.candidates?.first?.content?.parts?.first?.text else {
            throw GeminiError.malformedResponse
        }
        return text.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    var hasAPIKey: Bool {
        (KeychainStore.read(.geminiAPIKey) ?? "").isEmpty == false
    }

    // MARK: - Wire types
    //
    // Mirror the v1beta REST schema. Keep these private so we can swap to
    // a richer payload (system instructions, safety, tools) without
    // churning the public surface.

    private struct GenerateContentRequest: Encodable {
        let contents: [Content]
    }
    private struct Content: Codable {
        let parts: [Part]?
    }
    private struct Part: Codable {
        let text: String?
    }
    private struct GenerateContentResponse: Decodable {
        let candidates: [Candidate]?
    }
    private struct Candidate: Decodable {
        let content: Content?
    }
}
