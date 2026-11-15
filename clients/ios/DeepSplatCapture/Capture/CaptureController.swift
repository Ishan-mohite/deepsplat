import SwiftUI
import ARKit
import AVFoundation
import CoreImage
import DeepSplatCore
import Darwin

@MainActor
final class CaptureController: ObservableObject {
    enum State { case idle, recording, stopped, saved }
    @Published private(set) var state: State = .idle
    @Published private(set) var tracking = "not_available"
    @Published private(set) var elapsed: Double = 0
    @Published private(set) var frameCount = 0
    @Published private(set) var hasPhoto = false
    @Published private(set) var takingPhoto = false
    @Published private(set) var savedID: UUID?
    @Published private(set) var saveError: String?
    @Published var message: String?
    @Published private(set) var trackingWarning = false
    let ar = ARSessionController()
    private let location = LocationRecorder()
    private var engine: RecordingEngine!
    private var timer: Task<Void, Never>?
    private var gpsTask: Task<Void, Never>?
    private var trackingLostAt: Double?
    private var startedAt: Double = 0
    private(set) var configuration: CaptureConfiguration

    init(configuration: CaptureConfiguration) {
        self.configuration = configuration
        engine = RecordingEngine(ar: ar)
        engine.onCounts = { [weak self] count, photo in
            Task { @MainActor in self?.frameCount = count; self?.hasPhoto = photo }
        }
        engine.onPhoto = { [weak self] error in
            Task { @MainActor in
                guard let self else { return }
                self.takingPhoto = false
                if let error { self.message = error }
                if self.elapsed >= self.configuration.maxDuration && self.state == .recording {
                    if self.hasPhoto { self.stop() }
                    else { self.interrupt("The maximum duration was reached without a usable crack photo. Export the incomplete capture from Files or discard it.") }
                }
            }
        }
        engine.onSaved = { [weak self] id, error in
            Task { @MainActor in
                guard let self else { return }
                self.timer?.cancel(); self.location.cancel()
                if let error { self.saveError = error; self.message = error; self.state = .stopped }
                else { self.savedID = id; self.state = .saved }
            }
        }
        ar.onFrame = { [weak engine] frame in engine?.consume(frame) }
        ar.onTracking = { [weak self] state in
            Task { @MainActor in
                self?.tracking = state.value
                self?.trackingLostAt = state == .normal ? nil : self?.trackingLostAt ?? ProcessInfo.processInfo.systemUptime
                if state == .normal { self?.trackingWarning = false }
            }
        }
        ar.onFailure = { [weak self] error in Task { @MainActor in self?.interrupt(error) } }
    }
    func openCamera() async {
        guard await AVCaptureDevice.requestAccess(for: .video) else {
            message = "Enable Camera access in iPhone Settings to record an inspection."; return
        }
        do { try ar.run() } catch { message = error.localizedDescription }
    }
    func start() {
        guard state == .idle, tracking == "normal" else { return }
        guard !configuration.mapID.isEmpty else { message = "Set a map ID in Settings before recording."; return }
        state = .recording; startedAt = ProcessInfo.processInfo.systemUptime
        elapsed = 0; frameCount = 0; hasPhoto = false; saveError = nil
        engine.start(configuration)
        UIApplication.shared.isIdleTimerDisabled = true
        if configuration.recordGPS {
            gpsTask = Task { [weak self] in
                guard let self else { return }
                let fix = await self.location.fix()
                self.engine.setGeo(fix)
            }
        }
        timer = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 100_000_000)
                guard let self, self.state == .recording, !Task.isCancelled else { return }
                self.elapsed = ProcessInfo.processInfo.systemUptime - self.startedAt
                if let lost = self.trackingLostAt, ProcessInfo.processInfo.systemUptime - lost > 2 { self.trackingWarning = true }
                if self.elapsed >= self.configuration.maxDuration {
                    if self.takingPhoto { continue }
                    if self.hasPhoto { self.stop() }
                    else { self.takePhoto(automatic: true) }
                    return
                }
            }
        }
    }
    func takePhoto(automatic: Bool = false) {
        guard state == .recording, !takingPhoto else { return }
        takingPhoto = true; engine.takePhoto(automatic: automatic)
    }
    func stop() {
        guard state == .recording, hasPhoto, !takingPhoto else { return }
        state = .stopped; timer?.cancel(); location.cancel(); gpsTask?.cancel()
        UIApplication.shared.isIdleTimerDisabled = false
        engine.stop()
    }
    func interrupt(_ reason: String) {
        guard state == .recording else { message = reason; return }
        message = reason; state = .stopped; timer?.cancel(); location.cancel()
        UIApplication.shared.isIdleTimerDisabled = false
        engine.stop()
    }
    func close(discard: Bool) {
        timer?.cancel(); location.cancel(); gpsTask?.cancel(); ar.pause()
        UIApplication.shared.isIdleTimerDisabled = false
        if discard { engine.discard() }
    }
}

/// Mutable recording state, JPEG encoding and frame IO live only on ARSession's serial delegate queue.
private final class RecordingEngine {
    let ar: ARSessionController
    private let motion = MotionRecorder()
    private let context = CIContext(options: [.cacheIntermediates: false])
    private var writer: CaptureWriter?
    private var config: CaptureConfiguration?
    private var sampler = FrameSampler()
    private var startUptime: Double = 0, createdUnix: Double = 0, offset: Double = 0
    private var geo: GeoHint?
    private var recording = false
    private var finalizing = false
    var onCounts: ((Int, Bool) -> Void)?
    var onPhoto: ((String?) -> Void)?
    var onSaved: ((UUID?, String?) -> Void)?
    init(ar: ARSessionController) { self.ar = ar }

    func start(_ configuration: CaptureConfiguration) {
        ar.queue.async { [self] in
            do {
                let writer = try CaptureWriter(root: CapturePaths.captures)
                self.writer = writer; config = configuration; geo = nil
                startUptime = ProcessInfo.processInfo.systemUptime; createdUnix = Date().timeIntervalSince1970
                offset = createdUnix - startUptime
                sampler = FrameSampler(fps: configuration.fps, sourceFPS: ar.sourceFPS)
                try motion.start(folder: writer.folder, rate: configuration.imuRateHz, startTime: startUptime)
                recording = true
                writer.log("Recording started. Raw sensor images; no orientation correction. IMU clock: system uptime.")
            } catch { onSaved?(nil, error.localizedDescription) }
        }
    }
    func consume(_ frame: ARFrame) {
        guard recording, frame.timestamp >= startUptime, let writer, let config, sampler.shouldKeep(frame.timestamp) else { return }
        do {
            let snapshot = Snapshot(frame: frame, highRes: nil)
            try writer.appendFrame(jpeg: encode(snapshot, quality: config.jpegQuality), record: snapshot.record)
            onCounts?(writer.frames.count, writer.photo != nil)
        } catch {
            writer.log("Frame encoding/write failed: \(error.localizedDescription)")
            recording = false
            finish(error: error.localizedDescription)
        }
        // No ARFrame is stored or captured by an asynchronous closure.
    }
    func setGeo(_ value: GeoHint?) { ar.queue.async { [self] in geo = value } }
    func takePhoto(automatic: Bool) {
        ar.queue.async { [self] in
            guard recording else { onPhoto?("Recording is no longer active."); return }
            if automatic { writer?.log("Maximum duration reached; taking the required crack photo automatically.") }
            if ar.supportsHighResolution {
                ar.session.captureHighResolutionFrame { [weak self] frame, error in
                    guard let self else { return }
                    // Extract the pixel buffer and value metadata now, release ARFrame before queueing work.
                    if let frame { self.savePhoto(Snapshot(frame: frame, highRes: true), fallback: nil) }
                    else { self.fallbackPhoto(reason: error?.localizedDescription ?? "High-resolution capture returned no frame.") }
                }
            } else { fallbackPhoto(reason: "High-resolution capture is unavailable on this device.") }
        }
    }
    private func fallbackPhoto(reason: String) {
        guard let frame = ar.session.currentFrame else { ar.queue.async { [self] in onPhoto?("No camera frame is available.") }; return }
        savePhoto(Snapshot(frame: frame, highRes: false), fallback: reason)
    }
    private func savePhoto(_ snapshot: Snapshot, fallback: String?) {
        ar.queue.async { [self] in
            guard recording, let writer else { onPhoto?("Recording stopped before the photo was ready."); return }
            do {
                if let fallback { writer.log("Photo fallback: \(fallback)") }
                try writer.replacePhoto(jpeg: encode(snapshot, quality: 0.95), record: snapshot.record)
                writer.log("Crack photo saved; high_res=\(snapshot.record.highRes == true).")
                onCounts?(writer.frames.count, true); onPhoto?(nil)
            } catch { writer.log("Crack photo failed: \(error)"); onPhoto?(error.localizedDescription) }
        }
    }
    private func encode(_ snapshot: Snapshot, quality: Double) throws -> Data {
        let image = CIImage(cvPixelBuffer: snapshot.buffer).settingProperties([:])
        guard Int(image.extent.width) == snapshot.record.intrinsics.width,
              Int(image.extent.height) == snapshot.record.intrinsics.height else {
            throw CaptureError.invalid("Camera image dimensions do not match ARKit intrinsics.")
        }
        guard let colorSpace = CGColorSpace(name: CGColorSpace.sRGB),
              let data = context.jpegRepresentation(of: image, colorSpace: colorSpace, options: [kCGImageDestinationLossyCompressionQuality as CIImageRepresentationOption: quality]) else {
            throw CaptureError.invalid("JPEG encoding failed.")
        }
        return data
    }
    func stop() { ar.queue.async { [self] in guard recording else { return }; recording = false; finish(error: nil) } }
    private func finish(error: String?) {
        guard !finalizing, let writer, let config else { return }
        finalizing = true
        let duration = ProcessInfo.processInfo.systemUptime - startUptime
        motion.stop { [self] counts, motionError in
            ar.queue.async { [self] in
                defer { finalizing = false }
                do {
                    if let error { throw CaptureError.invalid(error) }
                    if let motionError { throw motionError }
                    guard let photo = writer.photo else { throw CaptureError.invalid("No crack photo was saved. The incomplete capture is available in Files.") }
                    guard counts.values.allSatisfy({ $0 > 0 }) else { throw CaptureError.invalid("Motion samples are missing. The incomplete capture is available in Files.") }
                    var system = utsname(); uname(&system)
                    let machineSize = MemoryLayout.size(ofValue: system.machine)
                    let device = withUnsafePointer(to: &system.machine) { pointer in
                        pointer.withMemoryRebound(to: CChar.self, capacity: machineSize) { String(cString: $0) }
                    }
                    let appVersion = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0.1.0"
                    let build = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "1"
                    let manifest = CaptureManifest(id: writer.id, mapID: config.mapID, createdUnix: createdUnix, offset: offset,
                        device: device, ios: ProcessInfo.processInfo.operatingSystemVersionString, app: "\(appVersion) (\(build))",
                        fps: config.fps, sourceFPS: ar.sourceFPS, duration: duration, frames: writer.frames, photo: photo,
                        imuRate: config.imuRateHz, sampleCounts: counts, geo: geo)
                    try writer.finish(manifest)
                    onSaved?(writer.id, nil)
                } catch { writer.log("Capture could not be finalized: \(error.localizedDescription)"); onSaved?(nil, error.localizedDescription) }
            }
        }
    }
    func discard() {
        ar.queue.async { [self] in
            recording = false
            motion.stop { [self] _, _ in ar.queue.async { [self] in
                if let writer { try? FileManager.default.removeItem(at: writer.folder) }
                writer = nil
            } }
        }
    }
}

private struct Snapshot {
    let buffer: CVPixelBuffer
    let record: FrameRecord
    init(frame: ARFrame, highRes: Bool?) {
        buffer = frame.capturedImage
        let camera = frame.camera
        record = FrameRecord(file: highRes == nil ? "" : "photo.jpg", timestamp: frame.timestamp,
            transform: camera.transform,
            intrinsics: Intrinsics(matrix: camera.intrinsics, width: Int(camera.imageResolution.width), height: Int(camera.imageResolution.height)),
            tracking: ARSessionController.tracking(camera.trackingState), highRes: highRes)
    }
}
