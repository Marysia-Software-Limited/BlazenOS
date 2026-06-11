import Foundation

/// M0 fallback intent matcher. Parses a *minimal* subset of the YAML
/// intent catalogue (just enough to drive the UI) and runs a regex
/// match per trigger.
///
/// **Not** a full YAML parser — only handles the catalogue shape used
/// by `configs/intents/*.yaml`. M1 replaces this with the Rust crate
/// via the FFI; the YAML stays the same.
final class PureSwiftIntents {

    private struct Entry {
        let name: String
        let action: String
        let patterns: [String: [NSRegularExpression]]
    }

    private var entries: [Entry] = []

    var count: Int { entries.count }

    func load(_ yaml: String) -> Bool {
        entries.removeAll()
        var currentName: String?
        var currentAction = "query"
        var currentLang: String?
        var triggers: [String: [NSRegularExpression]] = [:]

        func commit() {
            guard let n = currentName, !triggers.isEmpty else { return }
            entries.append(Entry(name: n, action: currentAction, patterns: triggers))
        }

        for rawLine in yaml.split(separator: "\n", omittingEmptySubsequences: false) {
            let line = rawLine.split(separator: "#", maxSplits: 1).first.map(String.init) ?? ""
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if trimmed.isEmpty { continue }

            if line.hasPrefix("  - name:") {
                commit()
                currentName = line.dropFirst("  - name:".count)
                    .trimmingCharacters(in: .whitespaces)
                currentAction = "query"
                currentLang = nil
                triggers.removeAll()
            } else if trimmed.hasPrefix("action:") {
                currentAction = trimmed.dropFirst("action:".count)
                    .trimmingCharacters(in: .whitespaces)
            } else if trimmed.hasPrefix("triggers:") {
                currentLang = nil
            } else if trimmed.hasPrefix("en:") || trimmed.hasPrefix("pl:") {
                let lang = String(trimmed.prefix(2))
                currentLang = lang
                let after = String(trimmed.dropFirst(3)).trimmingCharacters(in: .whitespaces)
                if after.hasPrefix("[") && after.hasSuffix("]") {
                    let patterns = parseInline(after)
                        .compactMap { try? NSRegularExpression(pattern: $0, options: [.caseInsensitive]) }
                    triggers[lang, default: []].append(contentsOf: patterns)
                }
            } else if trimmed.hasPrefix("- "), let lang = currentLang {
                let raw = String(trimmed.dropFirst(2)).trimmingCharacters(in: CharacterSet(charactersIn: "\"' "))
                if let re = try? NSRegularExpression(pattern: raw, options: [.caseInsensitive]) {
                    triggers[lang, default: []].append(re)
                }
            }
        }
        commit()
        return !entries.isEmpty
    }

    func match(transcript: String, language: String) -> IntentMatch? {
        let needle = transcript.lowercased()
        let range = NSRange(needle.startIndex..<needle.endIndex, in: needle)
        for e in entries {
            guard let patterns = e.patterns[language] else { continue }
            for p in patterns {
                if p.firstMatch(in: needle, options: [], range: range) != nil {
                    return IntentMatch(name: e.name, language: language, action: e.action)
                }
            }
        }
        return nil
    }

    private func parseInline(_ text: String) -> [String] {
        text
            .dropFirst().dropLast()
            .split(separator: ",")
            .map { $0.trimmingCharacters(in: CharacterSet(charactersIn: "\"' ")) }
            .filter { !$0.isEmpty }
    }
}
