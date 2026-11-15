import SwiftUI
import UIKit
import Network
import DeepSplatCore

@MainActor
final class UploadQueue: ObservableObject {
    @Published private(set) var records: [UploadRecord] = []
    @Published var message: String?
    @Published private(set) var storageHealthy = true
    private let settings: AppSettings
    private let delegate = TransferDelegate()
    private var session: URLSession!
    private var work: Task<Void, Never>?
    private var retryTimer: Task<Void, Never>?
    private var activeTransfer: UUID?
    private var restoring = true
    private let monitor = NWPathMonitor()
    private var wifiAvailable = false
    private var eventsDrained = false
    static let sessionID = "com.deepsplat.capture.uploads.v1"

    init(settings: AppSettings) {
        self.settings = settings
        do { records = try UploadPersistence.load(from: CapturePaths.queue) }
        catch { storageHealthy = false; message = "The upload queue could not be read. Existing files have been preserved: \(error.localizedDescription)" }
        delegate.onProgress = { [weak self] id, fraction in
            guard let self, let index = self.records.firstIndex(where: { $0.id == id }), self.records[index].status == .uploading else { return }
            // UI progress is in memory; phase boundaries are persisted.
            self.records[index].progress = min(0.99, fraction)
        }
        delegate.onComplete = { [weak self] id, code, error in self?.transferFinished(id: id, code: code, error: error) }
        delegate.onEventsFinished = { [weak self] in self?.eventsDrained = true; self?.finishBackgroundEventsIfReady() }
        let configuration = URLSessionConfiguration.background(withIdentifier: Self.sessionID)
        configuration.sessionSendsLaunchEvents = true; configuration.isDiscretionary = false
        configuration.waitsForConnectivity = true; configuration.timeoutIntervalForResource = 24 * 60 * 60
        session = URLSession(configuration: configuration, delegate: delegate, delegateQueue: nil)
        monitor.pathUpdateHandler = { [weak self] path in
            Task { @MainActor in
                self?.wifiAvailable = path.status == .satisfied && path.usesInterfaceType(.wifi)
                self?.kick()
            }
        }
        monitor.start(queue: DispatchQueue(label: "DeepSplat.Network"))
        Task { await restore() }
    }

    func refreshFiles() {
        guard storageHealthy else { return }
        do {
            try FileManager.default.createDirectory(at: CapturePaths.captures, withIntermediateDirectories: true)
            let folders = try FileManager.default.contentsOfDirectory(at: CapturePaths.captures, includingPropertiesForKeys: nil)
            for folder in folders where UUID(uuidString: folder.lastPathComponent) != nil {
                guard let manifest = try? CaptureWriter.readManifest(in: folder), !records.contains(where: { $0.id == manifest.captureID }) else { continue }
                records.append(UploadRecord(id: manifest.captureID, mapID: manifest.mapID, createdUnix: manifest.createdUnix))
            }
            records.sort { $0.createdUnix > $1.createdUnix }
            try persist()
        } catch { message = "Could not refresh captures: \(error.localizedDescription)" }
    }

    func enqueue(_ id: UUID) {
        guard storageHealthy, let index = records.firstIndex(where: { $0.id == id }), records[index].status != .uploaded else { return }
        if !records[index].queued {
            if !settings.mockBackend {
                guard BackendClient.baseURL(settings.backendURL) != nil, !settings.token().isEmpty else {
                    message = "Enter an HTTPS backend URL and token in Settings, or enable Mock backend."; return
                }
            }
            records[index].mock = settings.mockBackend
            records[index].backendURL = settings.backendURL
            records[index].wifiOnly = settings.wifiOnly
        }
        records[index].queued = true; records[index].nextAttempt = nil; records[index].error = nil
        do { try persist(); kick() } catch { message = error.localizedDescription }
    }
    func retry(_ id: UUID) { enqueue(id) }
    func delete(_ id: UUID) {
        guard let record = records.first(where: { $0.id == id }), record.status != .uploading else {
            message = "Wait for the active upload to finish before deleting this capture."; return
        }
        do {
            let next = records.filter { $0.id != id }
            try UploadPersistence.save(next, to: CapturePaths.queue)
            records = next
            let folder = CapturePaths.folder(id), archive = CapturePaths.archive(id)
            if FileManager.default.fileExists(atPath: folder.path) { try FileManager.default.removeItem(at: folder) }
            if FileManager.default.fileExists(atPath: archive.path) { try FileManager.default.removeItem(at: archive) }
        } catch { message = "Could not delete capture: \(error.localizedDescription)" }
    }
    func statusText(_ record: UploadRecord) -> String {
        if record.status == .uploaded { return record.mock ? "Saved to mock backend" : "Uploaded" + (record.backendStatus.map { " · \($0.rawValue)" } ?? "") }
        if record.queued, record.wifiOnly, !wifiAvailable { return "Waiting for Wi-Fi" }
        if record.status == .uploading {
            switch record.stage {
            case .preparing: return "Preparing archive"
            case .transferring: return "Uploading \(Int(record.progress * 100))%"
            case .confirming: return "Confirming upload"
            case .waiting: return "Waiting"
            }
        }
        if record.status == .failed { return "Upload failed · retry scheduled" }
        return record.queued ? "Queued" : "Saved on iPhone"
    }
    func export(_ id: UUID) async throws -> URL {
        let folder = CapturePaths.folder(id), destination = CapturePaths.archive(id)
        // A capture's contents are immutable after Stop; only the archive is regenerated.
        try await Task.detached(priority: .userInitiated) {
            try CaptureWriter.archive(folder: folder, destination: destination)
        }.value
        return destination
    }
    func refreshBackendStatuses() async {
        for record in records where record.status == .uploaded && !record.mock && record.backendStatus != .done {
            do {
                let status = try await client(record).status(record.id)
                try update(record.id) { $0.backendStatus = status.status }
            } catch { message = "Could not refresh backend status: \(error.localizedDescription)"; return }
        }
        kick()
    }
    private func restore() async {
        let tasks = await session.allTasks
        var live = Set<UUID>()
        for task in tasks {
            guard let text = task.taskDescription, let id = UUID(uuidString: text), records.contains(where: { $0.id == id }) else { task.cancel(); continue }
            live.insert(id); activeTransfer = id
        }
        for index in records.indices where records[index].status == .uploading && !live.contains(records[index].id) {
            records[index].status = .failed; records[index].backgroundTaskID = nil
            records[index].error = "The app was interrupted. Upload will retry using the same capture ID."
        }
        for index in records.indices where records[index].status == .failed && records[index].queued { records[index].nextAttempt = nil }
        do { try persist() } catch { message = error.localizedDescription }
        restoring = false; refreshFiles(); kick()
    }
    func kick() {
        guard !restoring, storageHealthy, work == nil, activeTransfer == nil,
              let record = UploadRecord.next(in: records) else { finishBackgroundEventsIfReady(); return }
        guard !record.wifiOnly || wifiAvailable || record.mock else { return }
        if let next = record.nextAttempt, next > Date() {
            retryTimer?.cancel()
            retryTimer = Task { [weak self] in
                try? await Task.sleep(nanoseconds: UInt64(max(0, next.timeIntervalSinceNow) * 1_000_000_000))
                guard !Task.isCancelled else { return }; self?.kick()
            }
            return
        }
        work = Task { [weak self] in
            guard let self else { return }
            let background = UIApplication.shared.beginBackgroundTask(withName: "Prepare DeepSplat upload", expirationHandler: nil)
            defer {
                if background != .invalid { UIApplication.shared.endBackgroundTask(background) }
                self.work = nil; self.kick(); self.finishBackgroundEventsIfReady()
            }
            do { try await self.prepare(record) }
            catch { self.fail(record.id, error: error) }
        }
    }
    private func prepare(_ record: UploadRecord) async throws {
        try update(record.id) { $0.status = .uploading; $0.stage = .preparing; $0.error = nil; $0.attempts += 1 }
        if record.hasUploadedBytes && !record.mock { try await confirm(record); return }
        let zip = CapturePaths.archive(record.id)
        if !FileManager.default.fileExists(atPath: zip.path) { _ = try await export(record.id) }
        if record.mock {
            try await Task.detached { try MockBackendClient().upload(id: record.id, archive: zip) }.value
            try update(record.id) { $0.status = .uploaded; $0.progress = 1; $0.queued = false; $0.stage = .waiting; $0.hasUploadedBytes = true }
            return
        }
        let ticket = try await client(record).ticket(record, zip: zip)
        let request = try UploadAPI.signedPut(ticket: ticket, wifiOnly: record.wifiOnly)
        // Never forward the backend bearer token to the signed storage URL.
        let task = session.uploadTask(with: request, fromFile: zip)
        task.taskDescription = record.id.uuidString
        do {
            try update(record.id) { $0.stage = .transferring; $0.backgroundTaskID = task.taskIdentifier; $0.progress = 0 }
        } catch { task.cancel(); throw error }
        activeTransfer = record.id
        task.resume()
    }
    private func transferFinished(id: UUID, code: Int?, error: String?) {
        guard records.contains(where: { $0.id == id }) else { return }
        activeTransfer = nil
        if let error {
            fail(id, error: CaptureError.invalid(error)); kick(); return
        }
        guard let code, (200...299).contains(code) else {
            fail(id, error: CaptureError.invalid("Storage upload failed (HTTP \(code ?? 0)). A fresh signed URL will be requested.")); kick(); return
        }
        do { try update(id) { $0.hasUploadedBytes = true; $0.stage = .confirming; $0.progress = 1; $0.backgroundTaskID = nil } }
        catch { message = error.localizedDescription; return }
        guard let record = records.first(where: { $0.id == id }) else { return }
        work = Task { [weak self] in
            guard let self else { return }
            let background = UIApplication.shared.beginBackgroundTask(withName: "Confirm DeepSplat upload", expirationHandler: nil)
            defer {
                if background != .invalid { UIApplication.shared.endBackgroundTask(background) }
                self.work = nil; self.kick(); self.finishBackgroundEventsIfReady()
            }
            do { try await self.confirm(record) }
            catch { self.fail(id, error: error) }
        }
    }
    private func confirm(_ record: UploadRecord) async throws {
        try update(record.id) { $0.stage = .confirming }
        let response = try await client(record).complete(record.id)
        try update(record.id) {
            $0.status = .uploaded; $0.stage = .waiting; $0.queued = false; $0.progress = 1
            $0.backendStatus = response.status; $0.backgroundTaskID = nil; $0.error = nil
        }
    }
    private func client(_ record: UploadRecord) throws -> BackendClient {
        guard let url = BackendClient.baseURL(record.backendURL) else { throw CaptureError.invalid("Invalid backend URL. Configure a valid HTTPS endpoint before queueing.") }
        return BackendClient(base: url, token: settings.token(for: record.backendURL))
    }
    private func fail(_ id: UUID, error: Error) {
        do { try update(id) {
            $0.status = .failed; $0.error = error.localizedDescription; $0.backgroundTaskID = nil
            $0.nextAttempt = Date().addingTimeInterval(UploadRecord.retryDelay(attempt: $0.attempts))
        } } catch { message = error.localizedDescription }
    }
    private func update(_ id: UUID, mutation: (inout UploadRecord) -> Void) throws {
        guard let index = records.firstIndex(where: { $0.id == id }) else { return }
        var next = records; mutation(&next[index])
        do { try UploadPersistence.save(next, to: CapturePaths.queue); records = next }
        catch { storageHealthy = false; throw error }
    }
    private func persist() throws {
        guard storageHealthy else { return }
        do { try UploadPersistence.save(records, to: CapturePaths.queue) }
        catch { storageHealthy = false; throw error }
    }
    private func finishBackgroundEventsIfReady() {
        guard eventsDrained, work == nil else { return }
        eventsDrained = false; BackgroundEvents.finish(Self.sessionID)
    }
}

private final class TransferDelegate: NSObject, URLSessionTaskDelegate, URLSessionDelegate, @unchecked Sendable {
    var onProgress: (@MainActor (UUID, Double) -> Void)?
    var onComplete: (@MainActor (UUID, Int?, String?) -> Void)?
    var onEventsFinished: (@MainActor () -> Void)?
    func urlSession(_ session: URLSession, task: URLSessionTask, didSendBodyData bytesSent: Int64, totalBytesSent: Int64, totalBytesExpectedToSend: Int64) {
        guard let text = task.taskDescription, let id = UUID(uuidString: text), totalBytesExpectedToSend > 0 else { return }
        let fraction = Double(totalBytesSent) / Double(totalBytesExpectedToSend)
        Task { @MainActor in onProgress?(id, fraction) }
    }
    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        guard let text = task.taskDescription, let id = UUID(uuidString: text) else { return }
        let code = (task.response as? HTTPURLResponse)?.statusCode, message = error?.localizedDescription
        Task { @MainActor in onComplete?(id, code, message) }
    }
    func urlSessionDidFinishEvents(forBackgroundURLSession session: URLSession) { Task { @MainActor in onEventsFinished?() } }
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        // Signed PUTs must not turn into redirected requests with changed methods or destinations.
        completionHandler(nil)
    }
}

@MainActor
enum BackgroundEvents {
    private static var handlers: [String: () -> Void] = [:]
    private static var finishedEarly = Set<String>()
    static func attach(_ id: String, handler: @escaping () -> Void) {
        if finishedEarly.remove(id) != nil { handler() } else { handlers[id] = handler }
    }
    static func finish(_ id: String) {
        if let handler = handlers.removeValue(forKey: id) { handler() } else { finishedEarly.insert(id) }
    }
}
