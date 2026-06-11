import XCTest
@testable import JessicaCore

final class JessicaCoreTests: XCTestCase {

    private let yaml = """
    version: 1
    intents:
      - name: volume_up
        triggers:
          en: ["louder"]
          pl: ["g(ł|l)o(ś|s)niej"]
        action: mutate
      - name: time_query
        triggers:
          en: ["what time is it"]
          pl: ["która godzina"]
        action: query
    """

    func test_loadsCatalogueAndExposesCount() {
        let core = JessicaCore()
        XCTAssertTrue(core.loadIntents(yaml))
        XCTAssertEqual(core.intentCount(), 2)
    }

    func test_matchesPolishTrigger() {
        let core = JessicaCore()
        core.loadIntents(yaml)
        let match = core.matchIntent(transcript: "głośniej", language: "pl")
        XCTAssertNotNil(match)
        XCTAssertEqual(match?.name, "volume_up")
        XCTAssertEqual(match?.language, "pl")
        XCTAssertEqual(match?.action, "mutate")
    }

    func test_matchesEnglishTrigger() {
        let core = JessicaCore()
        core.loadIntents(yaml)
        let match = core.matchIntent(transcript: "Louder, please", language: "en")
        XCTAssertEqual(match?.name, "volume_up")
    }

    func test_returnsNilWhenNothingMatches() {
        let core = JessicaCore()
        core.loadIntents(yaml)
        XCTAssertNil(core.matchIntent(transcript: "powiedz coś mądrego", language: "pl"))
    }
}
