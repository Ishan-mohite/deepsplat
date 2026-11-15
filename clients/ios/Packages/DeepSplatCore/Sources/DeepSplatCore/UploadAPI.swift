import Foundation

public struct CreateCaptureRequest: Codable, Sendable {
    public let captureID: UUID
    public let mapID: String
    public let sizeBytes: Int64
    public let createdUnix: Double
    enum CodingKeys: String, CodingKey { case captureID = "capture_id", mapID = "map_id", sizeBytes = "size_bytes", createdUnix = "created_unix" }
    public init(id: UUID, mapID: String, size: Int64, created: Double) { captureID = id; self.mapID = mapID; sizeBytes = size; createdUnix = created }
}
public struct UploadTicket: Decodable, Sendable {
    public let uploadURL: URL
    public let expiresAt: String?
    enum CodingKeys: String, CodingKey { case uploadURL = "upload_url", expiresAt = "expires_at" }
    public func validate() throws {
        guard uploadURL.scheme == "https", uploadURL.host != nil, uploadURL.user == nil, uploadURL.password == nil else {
            throw CaptureError.invalid("Backend returned an invalid signed upload URL.")
        }
    }
}
public struct BackendStatus: Decodable, Sendable {
    public enum Status: String, Codable, Sendable { case uploaded, processing, done, failed }
    public let status: Status
}
public enum UploadStatus: String, Codable, Sendable { case saved, uploading, uploaded, failed }
public enum UploadStage: String, Codable, Sendable { case waiting, preparing, transferring, confirming }
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
    public static func retryDelay(attempt: Int) -> TimeInterval { min(300, 2 * pow(2, Double(min(8, max(0, attempt - 1))))) }
    public static func next(in records: [Self]) -> Self? {
        records.filter { $0.queued && $0.status != .uploaded }.sorted { $0.createdUnix < $1.createdUnix }.first
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
