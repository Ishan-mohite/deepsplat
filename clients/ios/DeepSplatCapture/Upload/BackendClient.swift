import Foundation
import DeepSplatCore

struct BackendClient {
    let base: URL
    let token: String
    var transport: URLSession = .shared
    static func baseURL(_ text: String) -> URL? {
        guard let url = URL(string: text.trimmingCharacters(in: .whitespacesAndNewlines)),
              url.scheme == "https", url.host != nil, url.user == nil, url.password == nil,
              url.query == nil, url.fragment == nil else { return nil }
        return url
    }
    func ticket(_ record: UploadRecord, zip: URL) async throws -> UploadTicket {
        let size = try zip.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
        let body = CreateCaptureRequest(id: record.id, mapID: record.mapID, size: Int64(size), created: record.createdUnix)
        let ticket: UploadTicket = try await send(path: ["v1", "captures"], method: "POST", body: JSONEncoder().encode(body))
        try ticket.validate(); return ticket
    }
    func complete(_ id: UUID) async throws -> BackendStatus {
        try await send(path: ["v1", "captures", id.uuidString, "complete"], method: "POST")
    }
    func status(_ id: UUID) async throws -> BackendStatus {
        try await send(path: ["v1", "captures", id.uuidString], method: "GET")
    }
    private func send<T: Decodable>(path: [String], method: String, body: Data? = nil) async throws -> T {
        guard !token.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { throw CaptureError.invalid("Enter the backend token in Settings.") }
        var request = URLRequest(url: path.reduce(base) { $0.appendingPathComponent($1) })
        request.httpMethod = method; request.httpBody = body; request.timeoutInterval = 30
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if body != nil { request.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        let (data, response) = try await transport.data(for: request)
        guard let response = response as? HTTPURLResponse, (200...299).contains(response.statusCode) else {
            let code = (response as? HTTPURLResponse)?.statusCode ?? 0
            if code == 401 { throw CaptureError.invalid("Backend token was rejected (401). Update the token in Settings, then retry.") }
            throw CaptureError.invalid("Backend request failed (HTTP \(code)).")
        }
        return try JSONDecoder().decode(T.self, from: data)
    }
}

struct MockBackendClient {
    func upload(id: UUID, archive: URL) throws {
        let fm = FileManager.default
        try fm.createDirectory(at: CapturePaths.mockUploads, withIntermediateDirectories: true)
        let destination = CapturePaths.mockUploads.appendingPathComponent(id.uuidString + ".zip")
        let partial = CapturePaths.mockUploads.appendingPathComponent(id.uuidString + ".partial")
        if fm.fileExists(atPath: partial.path) { try fm.removeItem(at: partial) }
        try fm.copyItem(at: archive, to: partial)
        if fm.fileExists(atPath: destination.path) { _ = try fm.replaceItemAt(destination, withItemAt: partial) }
        else { try fm.moveItem(at: partial, to: destination) }
    }
}
