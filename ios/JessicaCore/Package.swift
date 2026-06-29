// swift-tools-version:5.9
// JessicaCore — Swift Package wrapping the Rust core (jessica-ffi).
//
// M0 ships a pure-Swift placeholder so the app compiles end-to-end
// without the Rust toolchain. M1 swaps this for a `binaryTarget(url:)`
// that points at `JessicaFFI.xcframework` produced by
// `make ffi` from `domains/jessica-ffi`.

import PackageDescription

let package = Package(
    name: "JessicaCore",
    platforms: [
        .iOS(.v17),
        .macOS(.v13),    // for `swift test` on the maintainer's mac
    ],
    products: [
        .library(name: "JessicaCore", targets: ["JessicaCore"]),
    ],
    targets: [
        .target(
            name: "JessicaCore",
            path: "Sources/JessicaCore"
        ),
        .testTarget(
            name: "JessicaCoreTests",
            dependencies: ["JessicaCore"],
            path: "Tests/JessicaCoreTests"
        ),
    ]
)
