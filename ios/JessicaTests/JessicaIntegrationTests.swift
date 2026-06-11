import XCTest
import JessicaCore

/// App-level integration smoke tests. The `JessicaCoreTests` package
/// exercises the Swift logic in isolation; this target wires the same
/// scenarios through the bundled `intents-system.yaml` resource and
/// confirms the app boot path works on the simulator.
final class JessicaIntegrationTests: XCTestCase {

    func test_bundledIntentsYamlLoads() throws {
        let url = try XCTUnwrap(
            Bundle.main.url(forResource: "intents-system", withExtension: "yaml"),
            "intents-system.yaml not bundled with the app"
        )
        let yaml = try String(contentsOf: url, encoding: .utf8)
        let core = JessicaCore()
        XCTAssertTrue(core.loadIntents(yaml))
        XCTAssertGreaterThan(core.intentCount(), 0)
    }
}
