// swift-tools-version: 5.9
import PackageDescription
let package = Package(name: "DeepSplatCore", platforms: [.iOS(.v16), .macOS(.v13)],
    products: [.library(name: "DeepSplatCore", targets: ["DeepSplatCore"])],
    targets: [.target(name: "DeepSplatCore"), .testTarget(name: "DeepSplatCoreTests", dependencies: ["DeepSplatCore"])])
