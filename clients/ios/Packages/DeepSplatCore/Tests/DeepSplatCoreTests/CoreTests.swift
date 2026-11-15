import XCTest
import simd
@testable import DeepSplatCore

final class CoreTests: XCTestCase {
    var root: URL!
    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    }
    override func tearDownWithError() throws { try FileManager.default.removeItem(at: root) }
    private func frame(file: String = "", highRes: Bool? = nil, timestamp: Double = 123) -> FrameRecord {
        let k = simd_float3x3(columns: (SIMD3(1400, 0, 0), SIMD3(0, 1401, 0), SIMD3(960, 720, 1)))
        return FrameRecord(file: file, timestamp: timestamp, transform: matrix_identity_float4x4,
                           intrinsics: Intrinsics(matrix: k, width: 1920, height: 1440), tracking: .normal, highRes: highRes)
    }
    private func completedWriter() throws -> (CaptureWriter, CaptureManifest) {
        let writer = try CaptureWriter(root: root)
        try writer.appendFrame(jpeg: Data([1, 2, 3]), record: frame())
        try writer.replacePhoto(jpeg: Data([4, 5, 6]), record: frame(highRes: true))
        let manifest = CaptureManifest(id: writer.id, mapID: "office", createdUnix: 1000, offset: 877,
            device: "iPhone", ios: "17.0", app: "0.1.0 (1)", fps: 5, sourceFPS: 60, duration: 1,
            frames: writer.frames, photo: try XCTUnwrap(writer.photo), imuRate: 100,
            sampleCounts: ["accel": 100, "gyro": 100, "motion": 100], geo: nil)
        try writer.finish(manifest)
        return (writer, manifest)
    }
    func testRowMajorTransformTranslationAndRoundTrip() throws {
        var matrix = simd_float4x4(columns: (SIMD4(1, 2, 3, 4), SIMD4(5, 6, 7, 8), SIMD4(9, 10, 11, 12), SIMD4(13, 14, 15, 16)))
        let values = FrameRecord.rowMajor(matrix)
        XCTAssertEqual(values, [1, 5, 9, 13, 2, 6, 10, 14, 3, 7, 11, 15, 4, 8, 12, 16])
        XCTAssertEqual(try FrameRecord.matrix(fromRowMajor: values), matrix)
        matrix = matrix_identity_float4x4; matrix.columns.3 = SIMD4(1, 2, 3, 1)
        let pose = FrameRecord.rowMajor(matrix)
        XCTAssertEqual(pose[3], 1); XCTAssertEqual(pose[7], 2); XCTAssertEqual(pose[11], 3)
    }
    func testInvalidPoseRejected() {
        XCTAssertThrowsError(try FrameRecord.matrix(fromRowMajor: [1, 2]))
        XCTAssertThrowsError(try FrameRecord.matrix(fromRowMajor: Array(repeating: .nan, count: 16)))
    }
    func testIntrinsicsUseColumnMajorSIMDIndices() {
        let value = frame().intrinsics
        XCTAssertEqual(value.fx, 1400); XCTAssertEqual(value.fy, 1401)
        XCTAssertEqual(value.cx, 960); XCTAssertEqual(value.cy, 720)
        XCTAssertEqual(value.width, 1920); XCTAssertEqual(value.height, 1440)
    }
    func testTrackingStrings() {
        XCTAssertEqual(TrackingState.normal.value, "normal")
        XCTAssertEqual(TrackingState.limited("excessive_motion").value, "limited:excessive_motion")
        XCTAssertEqual(TrackingState.notAvailable.value, "not_available")
    }
    func testFiveFPSAtThirtyAndSixtySourceFPSDoesNotDrift() {
        for source in [30.0, 60.0] {
            var sampler = FrameSampler(fps: 5, sourceFPS: source)
            let timestamps = (0..<Int(20 * source)).map { 1000 + Double($0) / source }
            let kept = timestamps.filter { sampler.shouldKeep($0) }
            XCTAssertEqual(kept.count, 100)
            for (a, b) in zip(kept, kept.dropFirst()) { XCTAssertEqual(b - a, 0.2, accuracy: 1 / source) }
        }
    }
    func testSamplerRejectsDuplicateBackwardAndNonFiniteTimes() {
        var sampler = FrameSampler()
        XCTAssertTrue(sampler.shouldKeep(10))
        XCTAssertFalse(sampler.shouldKeep(10)); XCTAssertFalse(sampler.shouldKeep(9))
        XCTAssertFalse(sampler.shouldKeep(.nan)); XCTAssertFalse(sampler.shouldKeep(.infinity))
        sampler.reset(); XCTAssertTrue(sampler.shouldKeep(1))
    }
    func testFaceUpSpecificForceSignAndMotionSignAreDifferent() {
        let faceUp = Vector3(0, 0, -1)
        XCTAssertEqual(ImuSample.specificForce(faceUp).z, 9.80665)
        XCTAssertEqual(ImuSample.acceleration(faceUp).z, -9.80665)
    }
    func testCSVBufferFlushesFinalPartialBatchAndKeepsMonotonicTimes() throws {
        let url = root.appendingPathComponent("accel.csv")
        let writer = try CSVWriter(url: url, header: ImuSample.accelHeader)
        for i in 0..<123 { try writer.append([100 + Double(i) / 100, 0, 0, 9.80665]) }
        try writer.close()
        let rows = try String(contentsOf: url).split(separator: "\n")
        XCTAssertEqual(rows.count, 124); XCTAssertEqual(rows[0], "t,ax,ay,az")
        XCTAssertTrue(rows.last!.hasPrefix("101.22,")); XCTAssertEqual(writer.count, 123)
    }
    func testCSVRejectsNonFiniteSample() throws {
        let writer = try CSVWriter(url: root.appendingPathComponent("imu.csv"), header: "t,x\n")
        XCTAssertThrowsError(try writer.append([1, .nan])); try writer.close()
    }
    func testCaptureLayoutPreservesBytesAndReplacesOnlyPhoto() throws {
        let writer = try CaptureWriter(root: root)
        try writer.appendFrame(jpeg: Data([1, 2]), record: frame())
        try writer.replacePhoto(jpeg: Data([3]), record: frame(highRes: false))
        try writer.replacePhoto(jpeg: Data([4, 5]), record: frame(highRes: true, timestamp: 124))
        XCTAssertEqual(writer.frames[0].file, "frames/000001.jpg")
        XCTAssertEqual(try Data(contentsOf: writer.folder.appendingPathComponent("frames/000001.jpg")), Data([1, 2]))
        XCTAssertEqual(try Data(contentsOf: writer.folder.appendingPathComponent("photo.jpg")), Data([4, 5]))
        XCTAssertEqual(writer.photo?.t, 124); XCTAssertEqual(writer.photo?.highRes, true)
        XCTAssertTrue(FileManager.default.fileExists(atPath: writer.folder.appendingPathComponent("imu/motion.csv").path))
    }
    func testManifestSnakeCaseNullGPSAndConventions() throws {
        let (writer, manifest) = try completedWriter()
        let data = try Data(contentsOf: writer.folder.appendingPathComponent("capture.json"))
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertEqual(json["schema_version"] as? Int, 1)
        XCTAssertTrue(json["geo"] is NSNull)
        XCTAssertEqual(json["uptime_to_unix_offset"] as? Double, 877)
        let video = try XCTUnwrap(json["video"] as? [String: Any])
        XCTAssertEqual(video["fps_target"] as? Int, 5)
        let photo = try XCTUnwrap(json["photo"] as? [String: Any])
        XCTAssertEqual(photo["interface_orientation"] as? String, "portrait")
        XCTAssertEqual(photo["high_res"] as? Bool, true)
        let reloaded = try CaptureWriter.readManifest(in: writer.folder)
        XCTAssertEqual(reloaded.captureID, manifest.captureID)
        XCTAssertEqual(reloaded.imu.sampleCounts["motion"], 100)
        XCTAssertTrue(reloaded.conventions["motion_gravity_and_user_acceleration"]!.contains("sign unchanged"))
    }
    func testMissingReferencedFilePreventsManifestPublication() throws {
        let (writer, manifest) = try completedWriter()
        try FileManager.default.removeItem(at: writer.folder.appendingPathComponent("photo.jpg"))
        XCTAssertThrowsError(try writer.finish(manifest))
    }
    func testArchiveReportsMeasuredProgressThroughCompletion() throws {
        let (writer, _) = try completedWriter()
        try Data(repeating: 0xA5, count: 2 * 1024 * 1024)
            .write(to: writer.folder.appendingPathComponent("frames/000001.jpg"))
        let progress = Progress(totalUnitCount: 0)
        var fractions: [Double] = []
        let observation = progress.observe(\.fractionCompleted, options: [.new]) { value, _ in
            fractions.append(value.fractionCompleted)
        }
        defer { observation.invalidate() }
        let zip = root.appendingPathComponent("archives/progress.zip")
        try CaptureWriter.archive(folder: writer.folder, destination: zip, progress: progress)
        XCTAssertTrue(fractions.contains { $0 > 0 && $0 < 1 })
        XCTAssertEqual(progress.fractionCompleted, 1, accuracy: 0.0001)
        XCTAssertGreaterThan(progress.totalUnitCount, 2 * 1024 * 1024)
        for (before, after) in Swift.zip(fractions, fractions.dropFirst()) {
            XCTAssertGreaterThanOrEqual(after + 0.000001, before)
        }
    }
    func testCancellingArchiveMidwayPreservesPreviousArchiveAndRemovesPartial() throws {
        let (writer, _) = try completedWriter()
        try Data(repeating: 0xA5, count: 2 * 1024 * 1024)
            .write(to: writer.folder.appendingPathComponent("frames/000001.jpg"))
        let destination = root.appendingPathComponent("archives/capture.zip")
        try FileManager.default.createDirectory(at: destination.deletingLastPathComponent(), withIntermediateDirectories: true)
        let original = Data("previous archive".utf8)
        try original.write(to: destination)
        let progress = Progress(totalUnitCount: 0)
        let observation = progress.observe(\.fractionCompleted, options: [.new]) { value, _ in
            if value.fractionCompleted > 0.1 { value.cancel() }
        }
        defer { observation.invalidate() }
        XCTAssertThrowsError(try CaptureWriter.archive(folder: writer.folder, destination: destination, progress: progress))
        XCTAssertTrue(progress.isCancelled)
        XCTAssertEqual(try Data(contentsOf: destination), original)
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath: destination.deletingLastPathComponent().path), ["capture.zip"])
    }

    func testArchiveIsARealZIPAndCanBeExportedTwice() throws {
        let (writer, _) = try completedWriter()
        let zip = root.appendingPathComponent("archives/capture.zip")
        try CaptureWriter.archive(folder: writer.folder, destination: zip)
        XCTAssertEqual(try Data(contentsOf: zip).prefix(2), Data([0x50, 0x4b]))
        try CaptureWriter.archive(folder: writer.folder, destination: zip)
        let process = Process(); process.executableURL = URL(fileURLWithPath: "/usr/bin/unzip")
        process.arguments = ["-t", zip.path]; let output = Pipe(); process.standardOutput = output; process.standardError = output
        try process.run(); process.waitUntilExit(); XCTAssertEqual(process.terminationStatus, 0)
        let text = String(data: output.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
        XCTAssertTrue(text.contains("capture.json")); XCTAssertTrue(text.contains("imu/accel.csv")); XCTAssertTrue(text.contains("photo.jpg"))
    }
    func testStoppedUploadPersistsAndDoesNotAutomaticallyRetry() throws {
        var record = UploadRecord(id: UUID(), mapID: "room", createdUnix: 1)
        record.status = .uploading; record.stage = .transferring; record.queued = true
        record.backgroundTaskID = 12; record.progress = 0.4
        record.nextAttempt = Date(); record.error = "Old transfer error"
        record.stopUpload()
        let queue = root.appendingPathComponent("stopped-queue.json")
        try UploadPersistence.save([record], to: queue)
        let restored = try XCTUnwrap(UploadPersistence.load(from: queue).first)
        XCTAssertEqual(restored.status, .stopped)
        XCTAssertFalse(restored.queued)
        XCTAssertNil(restored.backgroundTaskID)
        XCTAssertNil(restored.nextAttempt)
        XCTAssertNil(restored.error)
        XCTAssertEqual(restored.progress, 0)
        XCTAssertNil(UploadRecord.next(in: [restored]))
        XCTAssertFalse(restored.acceptsTransferCallback(taskID: 12))
    }
    func testRestartRejectsOldTransferCallbacksAndAllowsNextCapture() {
        var first = UploadRecord(id: UUID(), mapID: "room", createdUnix: 1)
        first.status = .uploading; first.queued = true; first.backgroundTaskID = 12
        XCTAssertTrue(first.acceptsTransferCallback(taskID: 12))
        first.stopUpload()
        var second = UploadRecord(id: UUID(), mapID: "room", createdUnix: 2)
        second.queued = true
        XCTAssertEqual(UploadRecord.next(in: [first, second])?.id, second.id)
        first.status = .uploading; first.queued = true; first.backgroundTaskID = 13
        XCTAssertFalse(first.acceptsTransferCallback(taskID: 12))
        XCTAssertTrue(first.acceptsTransferCallback(taskID: 13))
        first.status = .uploaded
        XCTAssertFalse(first.acceptsTransferCallback(taskID: 13))
    }
    func testStopDuringConfirmationRetainsUploadedBytesForRestart() {
        var record = UploadRecord(id: UUID(), mapID: "room", createdUnix: 1)
        record.status = .uploading; record.stage = .confirming; record.queued = true
        record.hasUploadedBytes = true; record.backendURL = "https://example.com"
        record.stopUpload()
        XCTAssertTrue(record.hasUploadedBytes)
        XCTAssertEqual(record.backendURL, "https://example.com")
        XCTAssertNil(UploadRecord.next(in: [record]))
    }

    func testUploadDiagnosticsRedactTokensAndSignedURLCredentials() {
        let text = "Bearer secret-token PUT https://user:password@storage.example.com/file.zip?signature=private#fragment failed"
        let safe = UploadDiagnostics.redacted(text, token: "secret-token")
        for secret in ["secret-token", "password", "signature", "private", "fragment", "user:"] {
            XCTAssertFalse(safe.contains(secret))
        }
        XCTAssertTrue(safe.contains("https://storage.example.com/file.zip"))
        XCTAssertTrue(safe.contains("failed"))
    }
    func testUploadDiagnosticsPersistTimesAttemptsAndBoundHistory() throws {
        let destination = root.appendingPathComponent("logs/upload.json")
        let entries = (0..<350).map { UploadLogEntry(message: "event \($0)", attempt: 2, duration: 1.25) }
        try UploadDiagnostics.save(entries, to: destination)
        let restored = try UploadDiagnostics.load(from: destination)
        XCTAssertEqual(restored.count, 300)
        XCTAssertEqual(restored.first?.message, "event 50")
        XCTAssertEqual(restored.last?.id, entries.last?.id)
        XCTAssertEqual(restored.last?.timestamp.timeIntervalSince1970 ?? 0, entries.last!.timestamp.timeIntervalSince1970, accuracy: 0.001)
        XCTAssertEqual(restored.last?.duration, 1.25)
        XCTAssertEqual(restored.last?.attempt, 2)
    }

    func testUploadPayloadUsesStableCaptureID() throws {
        let id = UUID()
        let request = CreateCaptureRequest(id: id, mapID: "room", size: 123, created: 1000)
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(request)) as? [String: Any])
        XCTAssertEqual(json["capture_id"] as? String, id.uuidString)
        XCTAssertEqual(json["size_bytes"] as? Int, 123)
        XCTAssertNil(json["token"])
    }
    func testSignedTicketRequiresHTTPSAndNoEmbeddedCredentials() throws {
        for url in ["http://example.com/file", "https://user:pass@example.com/file"] {
            let value = try JSONDecoder().decode(UploadTicket.self, from: JSONSerialization.data(withJSONObject: ["upload_url": url]))
            XCTAssertThrowsError(try value.validate())
        }
        let valid = try JSONDecoder().decode(UploadTicket.self, from: Data(#"{"upload_url":"https://storage.googleapis.com/bucket/key?signature=abc","expires_at":"2026-11-14T23:00:00Z"}"#.utf8))
        XCTAssertNoThrow(try valid.validate())
    }
    func testPlainHTTPOnlyForPrivateNetworkHosts() throws {
        for url in ["http://192.168.1.20:8000", "http://172.24.1.28:8000", "http://10.0.0.5", "http://mac.local:8000", "https://example.com"] {
            XCTAssertTrue(isAllowedBackendScheme(URL(string: url)!), url)
        }
        for url in ["http://example.com", "http://8.8.8.8", "http://172.32.0.1", "http://192.169.0.1", "ftp://192.168.1.1"] {
            XCTAssertFalse(isAllowedBackendScheme(URL(string: url)!), url)
        }
    }
    func testQueueOrderingPersistenceAndConfirmationRecovery() throws {
        let id = UUID()
        var old = UploadRecord(id: id, mapID: "room", createdUnix: 1)
        old.queued = true; old.status = .failed; old.hasUploadedBytes = true; old.stage = .confirming
        var new = UploadRecord(id: UUID(), mapID: "room", createdUnix: 2); new.queued = true
        let url = root.appendingPathComponent("queue.json")
        try UploadPersistence.save([new, old], to: url)
        let restored = try UploadPersistence.load(from: url)
        XCTAssertEqual(UploadRecord.next(in: restored)?.id, id)
        XCTAssertTrue(restored.first { $0.id == id }!.hasUploadedBytes)
        XCTAssertEqual(restored.first { $0.id == id }?.stage, .confirming)
    }
    func testRetryBackoffIsBounded() {
        XCTAssertEqual(UploadRecord.retryDelay(attempt: 1), 2)
        XCTAssertEqual(UploadRecord.retryDelay(attempt: 2), 4)
        XCTAssertEqual(UploadRecord.retryDelay(attempt: 1000), 300)
    }
    func testCorruptQueueIsNotSilentlyReplaced() throws {
        let url = root.appendingPathComponent("queue.json")
        let data = Data("not JSON".utf8); try data.write(to: url)
        XCTAssertThrowsError(try UploadPersistence.load(from: url))
        XCTAssertEqual(try Data(contentsOf: url), data)
    }
    func testSignedUploadDoesNotLeakBackendAuthorization() throws {
        let ticket = try JSONDecoder().decode(UploadTicket.self, from: Data(#"{"upload_url":"https://storage.googleapis.com/bucket/key?signature=abc"}"#.utf8))
        let request = try UploadAPI.signedPut(ticket: ticket, wifiOnly: true)
        XCTAssertEqual(request.httpMethod, "PUT")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Content-Type"), "application/zip")
        XCTAssertNil(request.value(forHTTPHeaderField: "Authorization"))
        XCTAssertEqual(request.url?.query, "signature=abc")
        XCTAssertFalse(request.allowsCellularAccess)
    }
    func testArchiveRejectsCaptureAfterAFileIsRemoved() throws {
        let (writer, _) = try completedWriter()
        try FileManager.default.removeItem(at: writer.folder.appendingPathComponent("imu/gyro.csv"))
        XCTAssertThrowsError(try CaptureWriter.archive(folder: writer.folder, destination: root.appendingPathComponent("broken.zip")))
    }

    func testGPSDistinguishesMSLFromEllipsoidAndEncodesUnknownAltitudesAsNull() throws {
        let fix = GeoHint(lat: 51, lon: 13, ellipsoidal: 160.2, msl: 113.1, horizontalAccuracy: 4, verticalAccuracy: 3, unix: 1000)
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(fix)) as? [String: Any])
        XCTAssertEqual(json["altitude_ellipsoidal_m"] as? Double, 160.2)
        XCTAssertEqual(json["altitude_msl_m"] as? Double, 113.1)
        let unknown = GeoHint(lat: 51, lon: 13, ellipsoidal: nil, msl: nil, horizontalAccuracy: 4, verticalAccuracy: -1, unix: 1000)
        let missing = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(unknown)) as? [String: Any])
        XCTAssertTrue(missing["altitude_ellipsoidal_m"] is NSNull)
        XCTAssertTrue(missing["altitude_msl_m"] is NSNull)
    }

}
