import Foundation
import ZIPFoundation

/// Confine each writer to the capture's serial queue.
public final class CaptureWriter {
    public let id: UUID
    public let folder: URL
    public private(set) var frames: [FrameRecord] = []
    public private(set) var photo: FrameRecord?
    public init(root: URL, id: UUID = UUID()) throws {
        self.id = id; folder = root.appendingPathComponent(id.uuidString, isDirectory: true)
        let fm = FileManager.default
        guard !fm.fileExists(atPath: folder.path) else { throw CaptureError.invalid("Capture already exists.") }
        try fm.createDirectory(at: folder.appendingPathComponent("frames"), withIntermediateDirectories: true)
        try fm.createDirectory(at: folder.appendingPathComponent("imu"), withIntermediateDirectories: true)
        try Data().write(to: folder.appendingPathComponent("log.txt"))
        for (name, header) in [("accel", ImuSample.accelHeader), ("gyro", ImuSample.gyroHeader), ("motion", ImuSample.motionHeader)] {
            try Data(header.utf8).write(to: folder.appendingPathComponent("imu/\(name).csv"))
        }
    }
    public func appendFrame(jpeg: Data, record: FrameRecord) throws {
        var record = record
        record.file = String(format: "frames/%06d.jpg", frames.count + 1)
        try jpeg.write(to: folder.appendingPathComponent(record.file), options: .atomic)
        frames.append(record)
    }
    public func replacePhoto(jpeg: Data, record: FrameRecord) throws {
        var record = record; record.file = "photo.jpg"
        try jpeg.write(to: folder.appendingPathComponent("photo.jpg"), options: .atomic)
        photo = record
    }
    public func log(_ text: String) {
        let entry = "[\(ProcessInfo.processInfo.systemUptime)] \(text)\n"
        print("DeepSplat: \(text)")
        do {
            let handle = try FileHandle(forWritingTo: folder.appendingPathComponent("log.txt"))
            defer { try? handle.close() }
            try handle.seekToEnd(); try handle.write(contentsOf: Data(entry.utf8))
        } catch { print("DeepSplat log error: \(error)") }
    }
    public func finish(_ manifest: CaptureManifest) throws {
        try manifest.validate()
        guard manifest.captureID == id else { throw CaptureError.invalid("Capture ID mismatch.") }
        try Self.validateContents(manifest, folder: folder)
        let encoder = JSONEncoder(); encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        try encoder.encode(manifest).write(to: folder.appendingPathComponent("capture.json"), options: .atomic)
        log("Saved \(frames.count) raw frames and one crack photo.")
    }
    public static func readManifest(in folder: URL) throws -> CaptureManifest {
        let value = try JSONDecoder().decode(CaptureManifest.self, from: Data(contentsOf: folder.appendingPathComponent("capture.json")))
        try value.validate(); try validateContents(value, folder: folder); return value
    }
    private static func validateContents(_ manifest: CaptureManifest, folder: URL) throws {
        let files = (manifest.video.frames + [manifest.photo]).map(\.file) + ["imu/accel.csv", "imu/gyro.csv", "imu/motion.csv", "log.txt"]
        for file in files {
            guard !file.contains(".."), !file.hasPrefix("/"), !file.contains("\\"),
                  FileManager.default.fileExists(atPath: folder.appendingPathComponent(file).path) else {
                throw CaptureError.invalid("A referenced capture file is missing or invalid: \(file)")
            }
        }
    }
    public static func archive(folder: URL, destination: URL, progress: Progress? = nil) throws {
        if progress?.isCancelled == true { throw CancellationError() }
        _ = try readManifest(in: folder)
        try FileManager.default.createDirectory(at: destination.deletingLastPathComponent(), withIntermediateDirectories: true)
        let partial = destination.deletingLastPathComponent().appendingPathComponent(UUID().uuidString + ".zip")
        defer { try? FileManager.default.removeItem(at: partial) }
        // JPEGs are already compressed. Stream them directly into a stored ZIP,
        // exposing byte-based progress without another temporary archive copy.
        try FileManager.default.zipItem(at: folder, to: partial, shouldKeepParent: true,
                                       compressionMethod: .none, progress: progress)
        if progress?.isCancelled == true { throw CancellationError() }
        if FileManager.default.fileExists(atPath: destination.path) {
            _ = try FileManager.default.replaceItemAt(destination, withItemAt: partial)
        } else { try FileManager.default.moveItem(at: partial, to: destination) }
    }
}
