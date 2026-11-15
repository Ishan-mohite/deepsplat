import Foundation
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
    @Published var tokenError: String?
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
    }
    var configuration: CaptureConfiguration {
        .init(mapID: mapID.trimmingCharacters(in: .whitespacesAndNewlines), fps: min(15, max(1, fps)),
              maxDuration: min(60, max(5, maxDuration)), imuRateHz: min(100, max(25, imuRateHz)),
              jpegQuality: min(1, max(0.5, jpegQuality)), recordGPS: recordGPS)
    }
    func token(for endpoint: String? = nil) -> String {
        KeychainToken.read(account: endpoint ?? backendURL)
    }
    func setToken(_ token: String) {
        do { try KeychainToken.write(token, account: backendURL); tokenError = nil }
        catch { tokenError = error.localizedDescription }
    }
}

private enum KeychainToken {
    static let service = "DeepSplatCapture.BackendToken"
    static func query(_ account: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: account]
    }
    static func read(account: String) -> String {
        var query = query(account)
        query[kSecReturnData as String] = true; query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess, let data = result as? Data else { return "" }
        return String(data: data, encoding: .utf8) ?? ""
    }
    static func write(_ value: String, account: String) throws {
        let query = query(account)
        if value.isEmpty {
            let status = SecItemDelete(query as CFDictionary)
            guard status == errSecSuccess || status == errSecItemNotFound else { throw keychainError(status) }; return
        }
        let attributes: [String: Any] = [kSecValueData as String: Data(value.utf8), kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly]
        let status = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
        if status == errSecItemNotFound {
            let add = SecItemAdd(query.merging(attributes) { _, new in new } as CFDictionary, nil)
            guard add == errSecSuccess else { throw keychainError(add) }
        } else if status != errSecSuccess { throw keychainError(status) }
    }
    static func keychainError(_ code: OSStatus) -> Error {
        NSError(domain: NSOSStatusErrorDomain, code: Int(code), userInfo: [NSLocalizedDescriptionKey: SecCopyErrorMessageString(code, nil) as String? ?? "Keychain write failed."])
    }
}

enum CapturePaths {
    static var documents: URL { FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0] }
    static var captures: URL { documents.appendingPathComponent("captures", isDirectory: true) }
    static var archives: URL { documents.appendingPathComponent("archives", isDirectory: true) }
    static var mockUploads: URL { documents.appendingPathComponent("mock-uploads", isDirectory: true) }
    static var queue: URL { FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("upload-queue.json") }
    static func folder(_ id: UUID) -> URL { captures.appendingPathComponent(id.uuidString, isDirectory: true) }
    static func archive(_ id: UUID) -> URL { archives.appendingPathComponent(id.uuidString + ".zip") }
}
