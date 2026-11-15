import Foundation
import DeepSplatCore

struct BackendClient {
    let base: URL
    let token: String
    var transport: URLSession = .shared
    var timeout: TimeInterval = 20
    var onEvent: (@Sendable (String, TimeInterval?) -> Void)?
    static func baseURL(_ text: String) -> URL? {
        guard let url = URL(string: text.trimmingCharacters(in: .whitespacesAndNewlines)),
              isAllowedBackendScheme(url), url.host != nil, url.user == nil, url.password == nil,
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
        request.httpMethod = method; request.httpBody = body; request.timeoutInterval = timeout
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if body != nil { request.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        let started = ProcessInfo.processInfo.systemUptime
        onEvent?("\(method) \(request.url!.absoluteString) · request \(body?.count ?? 0) bytes · timeout \(Int(timeout)) s", nil)
        let data: Data, response: URLResponse
        do { (data, response) = try await boundedResponse(for: request) }
        catch {
            onEvent?("\(method) \(request.url!.path) failed · \(error.localizedDescription)", ProcessInfo.processInfo.systemUptime - started)
            if Task.isCancelled { throw CancellationError() }
            let failure = error as NSError
            let address = request.url!.absoluteString
            if failure.domain == NSURLErrorDomain {
                switch failure.code {
                case NSURLErrorTimedOut:
                    throw CaptureError.invalid("Backend did not respond within \(Int(timeout)) seconds at \(address). Check the backend is running and reachable, then retry.")
                case NSURLErrorCannotFindHost, NSURLErrorDNSLookupFailed:
                    throw CaptureError.invalid("Cannot resolve the backend host at \(address). Check the backend URL or tunnel address.")
                case NSURLErrorCannotConnectToHost, NSURLErrorNetworkConnectionLost, NSURLErrorNotConnectedToInternet:
                    throw CaptureError.invalid("Cannot connect to \(address). Check the backend server, Wi-Fi or tunnel, and the app's Local Network permission.")
                default: break
                }
            }
            throw error
        }
        onEvent?("\(method) \(request.url!.path) · HTTP \((response as? HTTPURLResponse)?.statusCode ?? 0) · response \(data.count) bytes", ProcessInfo.processInfo.systemUptime - started)
        guard let response = response as? HTTPURLResponse, (200...299).contains(response.statusCode) else {
            let code = (response as? HTTPURLResponse)?.statusCode ?? 0
            if code == 401 { throw CaptureError.invalid("Backend token was rejected (401). Update the token in Settings, then retry.") }
            if code == 404 { throw CaptureError.invalid("Backend returned 404 for \(method) \(request.url!.path). Check the backend base URL and inspection map ID.") }
            throw CaptureError.invalid("Backend request failed (HTTP \(code)) at \(request.url!.path).")
        }
        do { return try JSONDecoder().decode(T.self, from: data) }
        catch { throw CaptureError.invalid("Backend returned an unexpected response for \(method) \(request.url!.path). Check that the URL points to the capture API, not the web viewer or a login page.") }
    }
    private func boundedResponse(for request: URLRequest) async throws -> (Data, URLResponse) {
        // timeoutInterval alone is an idle timeout and may be extended by trickling
        // responses. A separate deadline also cancels the underlying URLSession call.
        try await withThrowingTaskGroup(of: (Data, URLResponse).self) { group in
            group.addTask { try await transport.data(for: request) }
            group.addTask {
                try await Task.sleep(nanoseconds: UInt64(max(0, timeout) * 1_000_000_000))
                throw URLError(.timedOut)
            }
            defer { group.cancelAll() }
            return try await group.next()!
        }
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
