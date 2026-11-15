// Rasterize the canonical brand asset; never redraw or recolor the mark.
// Requires librsvg (`brew install librsvg`). Run from clients/ios.
import Foundation

let script = URL(fileURLWithPath: #filePath)
let ios = script.deletingLastPathComponent().deletingLastPathComponent()
let root = ios.deletingLastPathComponent().deletingLastPathComponent()
let source = root.appendingPathComponent("design/brand/logos/deepsplat-icon.svg")
let output = CommandLine.arguments.dropFirst().first.map { URL(fileURLWithPath: $0) }
    ?? ios.appendingPathComponent("DeepSplatCapture/Assets.xcassets/AppIcon.appiconset/AppIcon.png")
let process = Process()
process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
process.arguments = ["rsvg-convert", "--width", "1024", "--height", "1024",
                     "--background-color", "#172033", "--output", output.path, source.path]
try process.run()
process.waitUntilExit()
guard process.terminationStatus == 0 else { fatalError("Could not rasterize the brand icon. Install librsvg and retry.") }
print("Wrote \(output.path)")
