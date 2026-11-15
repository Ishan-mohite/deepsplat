import Foundation
import Combine
import Security

struct CaptureConfiguration: Sendable {
    let mapID: String
    let fps: Double
    let maxDuration: Double
    let imuRateHz: Double
    let jpegQuality: Double
    let recordGPS: Bool
}

@MainActor
final class AppSettings: ObservableObject {
    @Published var backendURL: String { didSet { defaults.set(backendURL, forKey: "backendURL") } }
    @Published var mapID: String { didSet { defaults.set(mapID, forKey: "mapID") } }
    @Published var fps: Double { didSet { defaults.set(fps, forKey: "fps") } }
    @Published var maxDuration: Double { didSet { defaults.set(maxDuration, forKey: "maxDuration") } }
    @Published var imuRateHz: Double { didSet { defaults.set(imuRateHz, forKey: "imuRateHz") } }
    @Published var jpegQuality: Double { didSet { defaults.set(jpegQuality, forKey: "jpegQuality") } }
    @Published var recordGPS: Bool { didSet { defaults.set(recordGPS, forKey: "recordGPS") } }
    @Published var wifiOnly: Bool { didSet { defaults.set(wifiOnly, forKey: "wifiOnly") } }
    @Published var mockBackend: Bool { didSet { defaults.set(mockBackend, forKey: "mockBackend") } }
    // Owned by the app, not the Settings sheet. Never written to preferences or disk.
    @Published private var sessionTokens: [String: String] = [:]
    private let defaults = UserDefaults.standard
    init() {
        let d = UserDefaults.standard
        backendURL = d.string(forKey: "backendURL") ?? ""
        mapID = d.string(forKey: "mapID") ?? ""
        fps = d.object(forKey: "fps") as? Double ?? 5
        maxDuration = d.object(forKey: "maxDuration") as? Double ?? 20
        imuRateHz = d.object(forKey: "imuRateHz") as? Double ?? 100
        jpegQuality = d.object(forKey: "jpegQuality") as? Double ?? 0.9
        recordGPS = d.bool(forKey: "recordGPS"); wifiOnly = d.bool(forKey: "wifiOnly")
        mockBackend = d.object(forKey: "mockBackend") as? Bool ?? true
        // Earlier versions persisted tokens. Retire those entries for session-only use.
        SecItemDelete([kSecClass as String: kSecClassGenericPassword,
                       kSecAttrService as String: "DeepSplatCapture.BackendToken"] as CFDictionary)
    }
    var configuration: CaptureConfiguration {
        .init(mapID: mapID.trimmingCharacters(in: .whitespacesAndNewlines), fps: min(15, max(1, fps)),
              maxDuration: min(60, max(5, maxDuration)), imuRateHz: min(100, max(25, imuRateHz)),
              jpegQuality: min(1, max(0.5, jpegQuality)), recordGPS: recordGPS)
    }
    func token(for endpoint: String? = nil) -> String {
        sessionTokens[endpoint ?? backendURL] ?? ""
    }
    func setToken(_ token: String) {
        sessionTokens[backendURL] = token.isEmpty ? nil : token
    }
}

enum CapturePaths {
    static var documents: URL { FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0] }
    static var captures: URL { documents.appendingPathComponent("captures", isDirectory: true) }
    static var archives: URL { documents.appendingPathComponent("archives", isDirectory: true) }
    static var mockUploads: URL { documents.appendingPathComponent("mock-uploads", isDirectory: true) }
    static var queue: URL { FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("upload-queue.json") }
    static var uploadLogs: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("upload-logs", isDirectory: true)
    }
    static func uploadLog(_ id: UUID) -> URL { uploadLogs.appendingPathComponent(id.uuidString + ".json") }
    static func folder(_ id: UUID) -> URL { captures.appendingPathComponent(id.uuidString, isDirectory: true) }
    static func archive(_ id: UUID) -> URL { archives.appendingPathComponent(id.uuidString + ".zip") }
}
