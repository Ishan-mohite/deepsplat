import SwiftUI
import UIKit
import Network
import DeepSplatCore

@MainActor
final class UploadQueue: ObservableObject {
    @Published private(set) var records: [UploadRecord] = []
    @Published private(set) var logs: [UUID: [UploadLogEntry]] = [:]
    @Published var message: String?
    @Published private(set) var storageHealthy = true
    private let settings: AppSettings
    private let delegate = TransferDelegate()
    private var session: URLSession!
    private var work: Task<Void, Never>?
    private var retryTimer: Task<Void, Never>?
    private var workID: UUID?
    private var activeTask: URLSessionTask?
    private var archiveProgress: Progress?
    private var activeTransfer: UUID?
    private var phaseTimes: [UUID: TimeInterval] = [:]
    private var attemptTimes: [UUID: TimeInterval] = [:]
    private var milestones: [String: Int] = [:]
    private var waitingForWiFi = Set<UUID>()
    private var restoring = true
    private let monitor = NWPathMonitor()
    private var wifiAvailable = false
    private var eventsDrained = false
    static let sessionID = "com.deepsplat.capture.uploads.v1"

    init(settings: AppSettings) {
        self.settings = settings
        do { records = try UploadPersistence.load(from: CapturePaths.queue) }
        catch { storageHealthy = false; message = "The upload queue could not be read. Existing files have been preserved: \(error.localizedDescription)" }
        delegate.onProgress = { [weak self] id, taskID, fraction in
            guard let self, let index = self.records.firstIndex(where: { $0.id == id }), self.records[index].acceptsTransferCallback(taskID: taskID) else { return }
            // UI progress is in memory; phase boundaries are persisted.
            self.records[index].progress = min(0.99, fraction)
            self.progressMilestone(id, phase: "Transfer", fraction: fraction)
        }
        delegate.onComplete = { [weak self] id, taskID, code, error in self?.transferFinished(id: id, taskID: taskID, code: code, error: error) }
        delegate.onDiagnostic = { [weak self] id, message, duration in self?.log(id, message, duration: duration) }
        delegate.onEventsFinished = { [weak self] in self?.eventsDrained = true; self?.finishBackgroundEventsIfReady() }
        let configuration = URLSessionConfiguration.background(withIdentifier: Self.sessionID)
        configuration.sessionSendsLaunchEvents = true; configuration.isDiscretionary = false
        configuration.waitsForConnectivity = true; configuration.timeoutIntervalForResource = 24 * 60 * 60
        session = URLSession(configuration: configuration, delegate: delegate, delegateQueue: nil)
        monitor.pathUpdateHandler = { [weak self] path in
            Task { @MainActor in
                if let self, self.wifiAvailable != (path.status == .satisfied && path.usesInterfaceType(.wifi)) {
                    for record in self.records where record.queued {
                        self.log(record.id, "Network changed · connection \(path.status == .satisfied ? "available" : "unavailable") · Wi-Fi \(path.usesInterfaceType(.wifi) ? "yes" : "no")")
                    }
                }
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
        if !records[index].queued && !records[index].hasUploadedBytes {
            if !settings.mockBackend {
                guard BackendClient.baseURL(settings.backendURL) != nil, !settings.token().isEmpty else {
                    message = "Enter an HTTPS backend URL and token in Settings, or enable Mock backend."
                    log(id, "Upload not queued · backend URL or session token missing/invalid")
                    return
                }
            }
            records[index].mock = settings.mockBackend
            records[index].backendURL = settings.backendURL
            records[index].wifiOnly = settings.wifiOnly
        }
        if records[index].status == .stopped { records[index].status = .saved }
        records[index].queued = true; records[index].nextAttempt = nil; records[index].error = nil
        do {
            try persist()
            log(id, "Upload queued · \(id.uuidString).zip · map \(records[index].mapID) · \(records[index].mock ? "mock" : "network") · Wi-Fi only \(records[index].wifiOnly ? "yes" : "no")")
            kick()
        } catch { message = error.localizedDescription }
    }
    func retry(_ id: UUID) { enqueue(id) }
    func stop(_ id: UUID) {
        guard let index = records.firstIndex(where: { $0.id == id }),
              records[index].status != .uploaded,
              records[index].queued || records[index].status == .uploading else { return }
        log(id, "Stop requested by user")
        do { try update(id) { $0.stopUpload() } }
        catch {
            // Still stop the network request if the queue cannot be written.
            records[index].stopUpload()
            message = "Upload stopped, but the stopped state could not be saved: \(error.localizedDescription)"
        }
        if workID == id { archiveProgress?.cancel(); work?.cancel() }
        if activeTransfer == id {
            activeTask?.cancel(); activeTask = nil; activeTransfer = nil
        }
        retryTimer?.cancel(); retryTimer = nil
        kick()
    }
    func delete(_ id: UUID) {
        guard let record = records.first(where: { $0.id == id }), record.status != .uploading, workID != id else {
            message = "Wait for the active upload to finish before deleting this capture."; return
        }
        do {
            let next = records.filter { $0.id != id }
            try UploadPersistence.save(next, to: CapturePaths.queue)
            records = next
            logs.removeValue(forKey: id); phaseTimes.removeValue(forKey: id); attemptTimes.removeValue(forKey: id)
            if FileManager.default.fileExists(atPath: CapturePaths.uploadLog(id).path) { try FileManager.default.removeItem(at: CapturePaths.uploadLog(id)) }
            let folder = CapturePaths.folder(id), archive = CapturePaths.archive(id)
            if FileManager.default.fileExists(atPath: folder.path) { try FileManager.default.removeItem(at: folder) }
            if FileManager.default.fileExists(atPath: archive.path) { try FileManager.default.removeItem(at: archive) }
        } catch { message = "Could not delete capture: \(error.localizedDescription)" }
    }
    func statusText(_ record: UploadRecord) -> String {
        if record.status == .stopped { return "Upload stopped" }
        if record.status == .uploaded { return record.mock ? "Saved to mock backend" : "Uploaded" + (record.backendStatus.map { " · \($0.rawValue)" } ?? "") }
        if record.queued, record.wifiOnly, !wifiAvailable { return "Waiting for Wi-Fi" }
        if record.status == .uploading {
            switch record.stage {
            case .preparing: return "Preparing archive \(Int(record.progress * 100))%"
            case .requestingUpload: return "Requesting upload URL"
            case .savingMock: return "Saving to mock backend"
            case .transferring: return "Uploading \(Int(record.progress * 100))%"
            case .confirming: return "Confirming upload"
            case .waiting: return "Waiting"
            }
        }
        if record.status == .failed { return record.queued ? "Upload failed · retry scheduled" : "Upload failed · retry needed" }
        return record.queued ? "Queued" : "Saved on iPhone"
    }
    func export(_ id: UUID) async throws -> URL {
        guard workID != id else { throw CaptureError.invalid("Wait for upload preparation to finish before exporting.") }
        return try await buildArchive(id)
    }
    private func buildArchive(_ id: UUID) async throws -> URL {
        let folder = CapturePaths.folder(id), destination = CapturePaths.archive(id)
        let archiveStarted = ProcessInfo.processInfo.systemUptime
        log(id, "Archive creation started · \(destination.lastPathComponent)")
        let progress = Progress(totalUnitCount: 0)
        let reportsUploadProgress = workID == id
        if reportsUploadProgress { archiveProgress = progress }
        let observer = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                if reportsUploadProgress, let self,
                   let index = self.records.firstIndex(where: { $0.id == id }),
                   self.records[index].status == .uploading, self.records[index].stage == .preparing {
                    self.records[index].progress = min(0.99, max(0, progress.fractionCompleted))
                    self.progressMilestone(id, phase: "Archive", fraction: progress.fractionCompleted)
                }
                try? await Task.sleep(nanoseconds: 100_000_000)
            }
        }
        defer {
            observer.cancel()
            if reportsUploadProgress { archiveProgress = nil }
        }
        try await withTaskCancellationHandler {
            try await Task.detached(priority: .userInitiated) {
                try CaptureWriter.archive(folder: folder, destination: destination, progress: progress)
            }.value
            try Task.checkCancellation()
        } onCancel: {
            progress.cancel()
        }
        if reportsUploadProgress, let index = records.firstIndex(where: { $0.id == id }), records[index].status == .uploading {
            records[index].progress = 1
        }
        let size = (try? destination.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0
        log(id, "Archive ready · \(size) bytes", duration: ProcessInfo.processInfo.systemUptime - archiveStarted)
        return destination
    }
    func refreshBackendStatuses() async {
        for record in records where record.status == .uploaded && !record.mock && record.backendStatus != .done {
            do {
                let status = try await client(record).status(record.id)
                try update(record.id) { $0.backendStatus = status.status }
                log(record.id, "Backend processing status · \(status.status.rawValue)")
            } catch { log(record.id, "Backend status refresh failed · \(error.localizedDescription)"); message = "Could not refresh backend status: \(error.localizedDescription)"; return }
        }
        kick()
    }
    private func restore() async {
        let tasks = await session.allTasks
        var live = Set<UUID>()
        for task in tasks {
            guard let text = task.taskDescription, let id = UUID(uuidString: text), records.contains(where: { $0.id == id && $0.acceptsTransferCallback(taskID: task.taskIdentifier) }) else { task.cancel(); continue }
            live.insert(id); activeTransfer = id; activeTask = task
            phaseTimes[id] = ProcessInfo.processInfo.systemUptime
            log(id, "Restored background transfer · task \(task.taskIdentifier) · timing starts at restoration")
        }
        for index in records.indices where records[index].status == .uploading && !live.contains(records[index].id) {
            records[index].status = .failed; records[index].backgroundTaskID = nil
            records[index].error = "The app was interrupted. Upload will retry using the same capture ID."
            log(records[index].id, "Previous attempt interrupted · no live background transfer")
        }
        for index in records.indices where records[index].status == .failed && records[index].queued { records[index].nextAttempt = nil }
        do { try persist() } catch { message = error.localizedDescription }
        restoring = false; refreshFiles(); kick()
    }
    func kick() {
        guard !restoring, storageHealthy, work == nil, activeTransfer == nil,
              let record = UploadRecord.next(in: records) else { finishBackgroundEventsIfReady(); return }
        guard !record.wifiOnly || wifiAvailable || record.mock else {
            if waitingForWiFi.insert(record.id).inserted { log(record.id, "Waiting for Wi-Fi") }
            return
        }
        waitingForWiFi.remove(record.id)
        if let next = record.nextAttempt, next > Date() {
            retryTimer?.cancel()
            retryTimer = Task { [weak self] in
                try? await Task.sleep(nanoseconds: UInt64(max(0, next.timeIntervalSinceNow) * 1_000_000_000))
                guard !Task.isCancelled else { return }; self?.kick()
            }
            return
        }
        workID = record.id
        work = Task { [weak self] in
            guard let self else { return }
            let background = UIApplication.shared.beginBackgroundTask(withName: "Prepare DeepSplat upload", expirationHandler: nil)
            defer {
                if background != .invalid { UIApplication.shared.endBackgroundTask(background) }
                self.work = nil; self.workID = nil; self.kick(); self.finishBackgroundEventsIfReady()
            }
            do { try await self.prepare(record) }
            catch { self.fail(record.id, error: error) }
        }
    }
    private func prepare(_ record: UploadRecord) async throws {
        try Task.checkCancellation()
        attemptTimes[record.id] = ProcessInfo.processInfo.systemUptime
        milestones["\(record.id)-Archive"] = nil; milestones["\(record.id)-Transfer"] = nil
        try update(record.id) { $0.status = .uploading; $0.stage = .preparing; $0.progress = 0; $0.error = nil; $0.attempts += 1 }
        if record.hasUploadedBytes && !record.mock { try await confirm(record); return }
        let zip = CapturePaths.archive(record.id)
        if !FileManager.default.fileExists(atPath: zip.path) { _ = try await buildArchive(record.id) }
        else {
            let size = (try? zip.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0
            log(record.id, "Reusing existing archive · \(zip.lastPathComponent) · \(size) bytes")
        }
        try Task.checkCancellation()
        if record.mock {
            try update(record.id) { $0.stage = .savingMock; $0.progress = 1 }
            try await Task.detached { try MockBackendClient().upload(id: record.id, archive: zip) }.value
            try Task.checkCancellation()
            try update(record.id) { $0.status = .uploaded; $0.progress = 1; $0.queued = false; $0.stage = .waiting; $0.hasUploadedBytes = true }
            return
        }
        try update(record.id) { $0.stage = .requestingUpload; $0.progress = 1 }
        let ticket = try await client(record).ticket(record, zip: zip)
        try Task.checkCancellation()
        log(record.id, "Upload URL received · storage host \(ticket.uploadURL.host ?? "unknown") · expiry \(ticket.expiresAt ?? "not supplied")")
        let request = try UploadAPI.signedPut(ticket: ticket, wifiOnly: record.wifiOnly)
        // Never forward the backend bearer token to the signed storage URL.
        let task = session.uploadTask(with: request, fromFile: zip)
        task.taskDescription = record.id.uuidString
        do {
            try update(record.id) { $0.stage = .transferring; $0.backgroundTaskID = task.taskIdentifier; $0.progress = 0 }
        } catch { task.cancel(); throw error }
        activeTransfer = record.id; activeTask = task
        log(record.id, "Background PUT started · task \(task.taskIdentifier) · Content-Type application/zip · bearer token excluded")
        task.resume()
    }
    private func transferFinished(id: UUID, taskID: Int, code: Int?, error: String?) {
        guard records.contains(where: { $0.id == id && $0.acceptsTransferCallback(taskID: taskID) }) else { return }
        log(id, "Background PUT finished · task \(taskID) · HTTP \(code.map(String.init) ?? "none")")
        activeTransfer = nil; activeTask = nil
        if let error {
            fail(id, error: CaptureError.invalid(error)); kick(); return
        }
        guard let code, (200...299).contains(code) else {
            fail(id, error: CaptureError.invalid("Storage upload failed (HTTP \(code ?? 0)). A fresh signed URL will be requested.")); kick(); return
        }
        do { try update(id) { $0.hasUploadedBytes = true; $0.stage = .confirming; $0.progress = 1; $0.backgroundTaskID = nil } }
        catch { message = error.localizedDescription; return }
        guard let record = records.first(where: { $0.id == id }) else { return }
        workID = id
        work = Task { [weak self] in
            guard let self else { return }
            let background = UIApplication.shared.beginBackgroundTask(withName: "Confirm DeepSplat upload", expirationHandler: nil)
            defer {
                if background != .invalid { UIApplication.shared.endBackgroundTask(background) }
                self.work = nil; self.workID = nil; self.kick(); self.finishBackgroundEventsIfReady()
            }
            do { try await self.confirm(record) }
            catch { self.fail(id, error: error) }
        }
    }
    private func confirm(_ record: UploadRecord) async throws {
        try Task.checkCancellation()
        try update(record.id) { $0.stage = .confirming }
        let response = try await client(record).complete(record.id)
        try Task.checkCancellation()
        try update(record.id) {
            $0.status = .uploaded; $0.stage = .waiting; $0.queued = false; $0.progress = 1
            $0.backendStatus = response.status; $0.backgroundTaskID = nil; $0.error = nil
        }
    }
    private func client(_ record: UploadRecord) throws -> BackendClient {
        guard let url = BackendClient.baseURL(record.backendURL) else { throw CaptureError.invalid("Invalid backend URL. Configure a valid HTTPS endpoint before queueing.") }
        return BackendClient(base: url, token: settings.token(for: record.backendURL), onEvent: { [weak self] message, duration in
            Task { @MainActor in self?.log(record.id, message, duration: duration) }
        })
    }
    private func fail(_ id: UUID, error: Error) {
        guard records.contains(where: { $0.id == id && $0.queued && $0.status == .uploading }) else { return }
        do { try update(id) {
            $0.status = .failed; $0.error = error.localizedDescription; $0.backgroundTaskID = nil
            if $0.stage == .requestingUpload {
                // Do not immediately hide configuration/connectivity errors behind
                // another request. The user can fix settings and explicitly retry.
                $0.queued = false; $0.nextAttempt = nil
            } else {
                $0.nextAttempt = Date().addingTimeInterval(UploadRecord.retryDelay(attempt: $0.attempts))
            }
        } }
        catch { message = error.localizedDescription }
    }
    private func update(_ id: UUID, mutation: (inout UploadRecord) -> Void) throws {
        guard let index = records.firstIndex(where: { $0.id == id }) else { return }
        var next = records; mutation(&next[index])
        let before = records[index]
        do { try UploadPersistence.save(next, to: CapturePaths.queue); records = next }
        catch { storageHealthy = false; throw error }
        if before.status != next[index].status || before.stage != next[index].stage {
            let now = ProcessInfo.processInfo.systemUptime
            let elapsed = phaseTimes.removeValue(forKey: id).map { max(0, now - $0) }
            let current = next[index]
            log(id, "\(before.stage.rawValue) → \(current.stage.rawValue) · \(current.status.rawValue)", duration: elapsed)
            if current.status == .uploading { phaseTimes[id] = now }
            if current.status == .failed {
                log(id, "Error · \(current.error ?? "Unknown error")")
                log(id, current.queued ? "Retry scheduled in \(Int(max(0, current.nextAttempt?.timeIntervalSinceNow ?? 0))) s" : "Waiting for explicit Retry upload")
            }
            if [.uploaded, .failed, .stopped].contains(current.status), let start = attemptTimes.removeValue(forKey: id) {
                log(id, "Attempt ended · \(current.status.rawValue)", duration: max(0, now - start))
            }
        }
    }
    private func persist() throws {
        guard storageHealthy else { return }
        do { try UploadPersistence.save(records, to: CapturePaths.queue) }
        catch { storageHealthy = false; throw error }
    }
    func phaseElapsed(_ id: UUID) -> TimeInterval {
        phaseTimes[id].map { max(0, ProcessInfo.processInfo.systemUptime - $0) } ?? 0
    }
    func loadLog(_ id: UUID) {
        guard logs[id] == nil else { return }
        do { logs[id] = try UploadDiagnostics.load(from: CapturePaths.uploadLog(id)) }
        catch { logs[id] = [UploadLogEntry(message: "Previous upload log could not be read", attempt: 0)] }
    }
    private func log(_ id: UUID, _ message: String, duration: TimeInterval? = nil) {
        // Ignore late diagnostics after the user has deleted the capture.
        guard let record = records.first(where: { $0.id == id }) else { return }
        loadLog(id)
        let safe = UploadDiagnostics.redacted(message, token: settings.token(for: record.backendURL))
        var entries = logs[id] ?? []
        entries.append(UploadLogEntry(message: safe, attempt: record.attempts, duration: duration))
        entries = Array(entries.suffix(UploadDiagnostics.maximumEntries))
        logs[id] = entries
        do { try UploadDiagnostics.save(entries, to: CapturePaths.uploadLog(id)) }
        catch { self.message = "Upload log could not be saved. The current log remains visible in this session." }
    }
    private func progressMilestone(_ id: UUID, phase: String, fraction: Double) {
        let bucket = min(4, max(0, Int(fraction * 4)))
        let key = "\(id)-\(phase)"
        guard bucket > (milestones[key] ?? 0) else { return }
        milestones[key] = bucket
        log(id, "\(phase) progress · \(bucket * 25)%")
    }
    private func finishBackgroundEventsIfReady() {
        guard eventsDrained, work == nil else { return }
        eventsDrained = false; BackgroundEvents.finish(Self.sessionID)
    }
}

private final class TransferDelegate: NSObject, URLSessionTaskDelegate, URLSessionDelegate, @unchecked Sendable {
    var onProgress: (@MainActor (UUID, Int, Double) -> Void)?
    var onComplete: (@MainActor (UUID, Int, Int?, String?) -> Void)?
    var onEventsFinished: (@MainActor () -> Void)?
    var onDiagnostic: (@MainActor (UUID, String, TimeInterval?) -> Void)?
    func urlSession(_ session: URLSession, task: URLSessionTask, didSendBodyData bytesSent: Int64, totalBytesSent: Int64, totalBytesExpectedToSend: Int64) {
        guard let text = task.taskDescription, let id = UUID(uuidString: text), totalBytesExpectedToSend > 0 else { return }
        let fraction = Double(totalBytesSent) / Double(totalBytesExpectedToSend)
        let taskID = task.taskIdentifier
        Task { @MainActor in onProgress?(id, taskID, fraction) }
    }
    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        guard let text = task.taskDescription, let id = UUID(uuidString: text) else { return }
        let code = (task.response as? HTTPURLResponse)?.statusCode, message = error?.localizedDescription
        let taskID = task.taskIdentifier
        let summary = "Transfer byte totals · task \(taskID) · sent \(task.countOfBytesSent) / expected \(task.countOfBytesExpectedToSend) · received \(task.countOfBytesReceived)"
        Task { @MainActor in onDiagnostic?(id, summary, nil) }
        Task { @MainActor in onComplete?(id, taskID, code, message) }
    }
    func urlSession(_ session: URLSession, taskIsWaitingForConnectivity task: URLSessionTask) {
        guard let text = task.taskDescription, let id = UUID(uuidString: text) else { return }
        let message = "Transfer waiting for connectivity · task \(task.taskIdentifier)"
        Task { @MainActor in onDiagnostic?(id, message, nil) }
    }
    func urlSession(_ session: URLSession, task: URLSessionTask, didFinishCollecting metrics: URLSessionTaskMetrics) {
        guard let text = task.taskDescription, let id = UUID(uuidString: text) else { return }
        var parts = ["Transfer metrics · task \(task.taskIdentifier) · redirects \(metrics.redirectCount)"]
        for (index, transaction) in metrics.transactionMetrics.enumerated() {
            parts.append("connection \(index + 1) · \(transaction.networkProtocolName ?? "protocol unknown")")
            for (label, start, end) in [
                ("DNS", transaction.domainLookupStartDate, transaction.domainLookupEndDate),
                ("connect", transaction.connectStartDate, transaction.connectEndDate),
                ("TLS", transaction.secureConnectionStartDate, transaction.secureConnectionEndDate),
                ("request to first byte", transaction.requestStartDate, transaction.responseStartDate)
            ] {
                if let start, let end { parts.append(String(format: "\(label) %.3f s", end.timeIntervalSince(start))) }
            }
        }
        let message = parts.joined(separator: " · ")
        let duration = metrics.taskInterval.duration
        Task { @MainActor in onDiagnostic?(id, message, duration) }
    }
    func urlSessionDidFinishEvents(forBackgroundURLSession session: URLSession) { Task { @MainActor in onEventsFinished?() } }
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        // Signed PUTs must not turn into redirected requests with changed methods or destinations.
        if let text = task.taskDescription, let id = UUID(uuidString: text) {
            let message = "Transfer redirect rejected · HTTP \(response.statusCode) · task \(task.taskIdentifier)"
            Task { @MainActor in onDiagnostic?(id, message, nil) }
        }
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
