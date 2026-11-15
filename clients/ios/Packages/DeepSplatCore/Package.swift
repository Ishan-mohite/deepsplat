// swift-tools-version: 5.9
import PackageDescription
let package = Package(name: "DeepSplatCore", platforms: [.iOS(.v16), .macOS(.v13)],
    products: [.library(name: "DeepSplatCore", targets: ["DeepSplatCore"])],
    dependencies: [.package(url: "https://github.com/weichsel/ZIPFoundation.git", exact: "0.9.20")],
    targets: [.target(name: "DeepSplatCore", dependencies: ["ZIPFoundation"]), .testTarget(name: "DeepSplatCoreTests", dependencies: ["DeepSplatCore"])])
