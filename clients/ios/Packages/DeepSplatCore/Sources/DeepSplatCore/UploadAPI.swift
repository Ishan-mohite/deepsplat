import Foundation

public struct CreateCaptureRequest: Codable, Sendable {
    public let captureID: UUID
    public let mapID: String
    public let sizeBytes: Int64
    public let createdUnix: Double
    enum CodingKeys: String, CodingKey { case captureID = "capture_id", mapID = "map_id", sizeBytes = "size_bytes", createdUnix = "created_unix" }
    public init(id: UUID, mapID: String, size: Int64, created: Double) { captureID = id; self.mapID = mapID; sizeBytes = size; createdUnix = created }
}
/// Plain http is accepted only for private-network hosts (same Wi-Fi testing); everything else must be https.
public func isPrivateNetworkHost(_ host: String?) -> Bool {
    guard let host = host?.lowercased() else { return false }
    if host == "localhost" || host.hasSuffix(".local") { return true }
    let p = host.split(separator: ".").compactMap { Int($0) }
    guard p.count == 4, p.allSatisfy({ (0...255).contains($0) }) else { return false }
    return p[0] == 10 || (p[0] == 172 && (16...31).contains(p[1])) || (p[0] == 192 && p[1] == 168)
        || (p[0] == 169 && p[1] == 254) || p[0] == 127
}
public func isAllowedBackendScheme(_ url: URL) -> Bool {
    url.scheme == "https" || (url.scheme == "http" && isPrivateNetworkHost(url.host))
}
public struct UploadTicket: Decodable, Sendable {
    public let uploadURL: URL
    public let expiresAt: String?
    enum CodingKeys: String, CodingKey { case uploadURL = "upload_url", expiresAt = "expires_at" }
    public func validate() throws {
        guard isAllowedBackendScheme(uploadURL), uploadURL.host != nil, uploadURL.user == nil, uploadURL.password == nil else {
            throw CaptureError.invalid("Backend returned an invalid signed upload URL.")
        }
    }
}
public struct BackendStatus: Decodable, Sendable {
    public enum Status: String, Codable, Sendable { case uploaded, processing, done, failed }
    public let status: Status
}
public enum UploadStatus: String, Codable, Sendable { case saved, uploading, uploaded, failed, stopped }
public enum UploadStage: String, Codable, Sendable { case waiting, preparing, requestingUpload, savingMock, transferring, confirming }
public struct UploadRecord: Identifiable, Codable, Sendable {
    public let id: UUID
    public let mapID: String
    public let createdUnix: Double
    public var status: UploadStatus = .saved
    public var stage: UploadStage = .waiting
    public var queued = false
    public var mock = false
    public var wifiOnly = false
    public var backendURL: String = ""
    public var attempts = 0
    public var nextAttempt: Date?
    public var progress: Double = 0
    public var error: String?
    public var backendStatus: BackendStatus.Status?
    public var backgroundTaskID: Int?
    public var hasUploadedBytes = false
    public init(id: UUID, mapID: String, createdUnix: Double) { self.id = id; self.mapID = mapID; self.createdUnix = createdUnix }
    /// Keep the capture and any completed storage PUT, but require an explicit restart.
    public mutating func stopUpload() {
        status = .stopped; stage = .waiting; queued = false
        backgroundTaskID = nil; nextAttempt = nil; progress = 0; error = nil
    }
    /// A cancelled transfer may report progress/completion after a new attempt starts.
    public func acceptsTransferCallback(taskID: Int) -> Bool {
        queued && status == .uploading && backgroundTaskID == taskID
    }
    public static func retryDelay(attempt: Int) -> TimeInterval { min(300, 2 * pow(2, Double(min(8, max(0, attempt - 1))))) }
    public static func next(in records: [Self]) -> Self? {
        records.filter { $0.queued && $0.status != .uploaded && $0.status != .stopped }.sorted { $0.createdUnix < $1.createdUnix }.first
    }
}
public enum UploadPersistence {
    public static func load(from url: URL) throws -> [UploadRecord] {
        guard FileManager.default.fileExists(atPath: url.path) else { return [] }
        return try JSONDecoder().decode([UploadRecord].self, from: Data(contentsOf: url))
    }
    public static func save(_ records: [UploadRecord], to url: URL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try JSONEncoder().encode(records).write(to: url, options: .atomic)
    }
}

public enum UploadAPI {
    public static func signedPut(ticket: UploadTicket, wifiOnly: Bool) throws -> URLRequest {
        try ticket.validate()
        var request = URLRequest(url: ticket.uploadURL)
        request.httpMethod = "PUT"
        request.setValue("application/zip", forHTTPHeaderField: "Content-Type")
        request.allowsCellularAccess = !wifiOnly
        request.allowsExpensiveNetworkAccess = !wifiOnly
        return request
    }
}

/// Local upload diagnostics, separate from the immutable capture dataset.
public struct UploadLogEntry: Identifiable, Codable, Sendable {
    public let id: UUID
    public let timestamp: Date
    public let attempt: Int
    public let message: String
    public let duration: TimeInterval?
    public init(message: String, attempt: Int, duration: TimeInterval? = nil) {
        id = UUID(); timestamp = Date(); self.attempt = attempt
        self.message = message; self.duration = duration
    }
}

public enum UploadDiagnostics {
    public static let maximumEntries = 300
    public static func redacted(_ text: String, token: String = "") -> String {
        var result = token.isEmpty ? text : text.replacingOccurrences(of: token, with: "[redacted]")
        // Errors can contain signed URLs: never persist credentials, query or fragment.
        let pattern = try! NSRegularExpression(pattern: "https?://[^\\s\\\"<>]+", options: .caseInsensitive)
        for match in pattern.matches(in: result, range: NSRange(result.startIndex..., in: result)).reversed() {
            guard let range = Range(match.range, in: result), var url = URLComponents(string: String(result[range])) else { continue }
            url.user = nil; url.password = nil; url.query = nil; url.fragment = nil
            result.replaceSubrange(range, with: url.string ?? "[URL redacted]")
        }
        result = result.replacingOccurrences(of: "Bearer\\s+[^\\s,;]+", with: "Bearer [redacted]", options: [.regularExpression, .caseInsensitive])
        return String(result.prefix(2000))
    }
    public static func load(from url: URL) throws -> [UploadLogEntry] {
        guard FileManager.default.fileExists(atPath: url.path) else { return [] }
        return Array(try JSONDecoder().decode([UploadLogEntry].self, from: Data(contentsOf: url)).suffix(maximumEntries))
    }
    public static func save(_ entries: [UploadLogEntry], to url: URL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try JSONEncoder().encode(Array(entries.suffix(maximumEntries))).write(to: url, options: .atomic)
    }
}
