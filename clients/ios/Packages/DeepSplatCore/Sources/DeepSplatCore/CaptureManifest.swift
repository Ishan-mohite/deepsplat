import Foundation

public struct GeoHint: Codable, Sendable {
    public let lat: Double, lon: Double
    public let altitudeEllipsoidalM: Double?, altitudeMslM: Double?
    public let horizontalAccuracyM: Double, verticalAccuracyM: Double, tUnix: Double
    enum CodingKeys: String, CodingKey {
        case lat, lon
        case altitudeEllipsoidalM = "altitude_ellipsoidal_m", altitudeMslM = "altitude_msl_m"
        case horizontalAccuracyM = "horizontal_accuracy_m", verticalAccuracyM = "vertical_accuracy_m", tUnix = "t_unix"
    }
    public init(lat: Double, lon: Double, ellipsoidal: Double?, msl: Double?, horizontalAccuracy: Double, verticalAccuracy: Double, unix: Double) {
        self.lat = lat; self.lon = lon; altitudeEllipsoidalM = ellipsoidal; altitudeMslM = msl
        horizontalAccuracyM = horizontalAccuracy; verticalAccuracyM = verticalAccuracy; tUnix = unix
    }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(lat, forKey: .lat); try c.encode(lon, forKey: .lon)
        try c.encode(altitudeEllipsoidalM, forKey: .altitudeEllipsoidalM)
        try c.encode(altitudeMslM, forKey: .altitudeMslM)
        try c.encode(horizontalAccuracyM, forKey: .horizontalAccuracyM)
        try c.encode(verticalAccuracyM, forKey: .verticalAccuracyM); try c.encode(tUnix, forKey: .tUnix)
    }
}

public struct CaptureManifest: Codable, Sendable {
    public struct Video: Codable, Sendable {
        public let fpsTarget: Double, sourceFPS: Double, durationS: Double
        public let frames: [FrameRecord]
        enum CodingKeys: String, CodingKey { case fpsTarget = "fps_target", sourceFPS = "source_fps", durationS = "duration_s", frames }
    }
    public struct IMU: Codable, Sendable {
        public let rateHz: Double
        public let files = ["accel": "imu/accel.csv", "gyro": "imu/gyro.csv", "motion": "imu/motion.csv"]
        public let motionReferenceFrame = "xArbitraryCorrectedZVertical"
        public let sampleCounts: [String: Int]
        enum CodingKeys: String, CodingKey { case rateHz = "rate_hz", files, motionReferenceFrame = "motion_reference_frame", sampleCounts = "sample_counts" }
        public init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            rateHz = try c.decode(Double.self, forKey: .rateHz)
            sampleCounts = try c.decodeIfPresent([String: Int].self, forKey: .sampleCounts) ?? [:]
        }
        public init(rateHz: Double, sampleCounts: [String: Int]) { self.rateHz = rateHz; self.sampleCounts = sampleCounts }
    }
    public let schemaVersion: Int
    public let captureID: UUID
    public let mapID: String
    public let createdUnix: Double
    public let device: String, iosVersion: String, appVersion: String
    public let timeBase: String
    public let uptimeToUnixOffset: Double
    public let conventions: [String: String]
    public let video: Video
    public let photo: FrameRecord
    public let imu: IMU
    public let geo: GeoHint?
    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version", captureID = "capture_id", mapID = "map_id", createdUnix = "created_unix"
        case device, iosVersion = "ios_version", appVersion = "app_version", timeBase = "time_base", uptimeToUnixOffset = "uptime_to_unix_offset"
        case conventions, video, photo, imu, geo
    }
    public init(id: UUID, mapID: String, createdUnix: Double, offset: Double, device: String, ios: String, app: String,
                fps: Double, sourceFPS: Double, duration: Double, frames: [FrameRecord], photo: FrameRecord,
                imuRate: Double, sampleCounts: [String: Int], geo: GeoHint?) {
        schemaVersion = 1; captureID = id; self.mapID = mapID; self.createdUnix = createdUnix
        uptimeToUnixOffset = offset; self.device = device; iosVersion = ios; appVersion = app
        timeBase = "seconds of system uptime"
        conventions = [
            "images": "raw camera sensor orientation (landscape, charging port right), no EXIF orientation",
            "camera_to_world": "ARKit camera.transform unchanged, row-major 4x4; world right-handed, metric, +Y up, origin at session start; camera +X right, +Y up, looking along -Z",
            "intrinsics": "pixels, for the stored image size",
            "imu_axes": "device frame: +X right, +Y top of phone, +Z out of screen",
            "accel": "specific force in m/s^2, a = -raw * 9.80665; face up reads +9.81 on Z",
            "motion_gravity_and_user_acceleration": "CoreMotion sign unchanged, multiplied by 9.80665, m/s^2",
            "gyro": "CoreMotion rotation rate unchanged, rad/s"
        ]
        video = Video(fpsTarget: fps, sourceFPS: sourceFPS, durationS: duration, frames: frames)
        self.photo = photo; imu = IMU(rateHz: imuRate, sampleCounts: sampleCounts); self.geo = geo
    }
    public func validate() throws {
        guard schemaVersion == 1, !mapID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              !video.frames.isEmpty, video.durationS >= 0, video.durationS.isFinite,
              createdUnix.isFinite, uptimeToUnixOffset.isFinite else { throw CaptureError.invalid("Capture is missing its map, frames, or time base.") }
        for frame in video.frames + [photo] {
            _ = try FrameRecord.matrix(fromRowMajor: frame.cameraToWorld)
            guard frame.t.isFinite, frame.intrinsics.width > 0, frame.intrinsics.height > 0,
                  frame.intrinsics.fx > 0, frame.intrinsics.fy > 0,
                  frame.intrinsics.cx.isFinite, frame.intrinsics.cy.isFinite,
                  frame.interfaceOrientation == "portrait" else { throw CaptureError.invalid("Invalid frame metadata.") }
        }
        guard photo.file == "photo.jpg", photo.highRes != nil else { throw CaptureError.invalid("Capture requires a crack photo.") }
    }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(schemaVersion, forKey: .schemaVersion); try c.encode(captureID, forKey: .captureID)
        try c.encode(mapID, forKey: .mapID); try c.encode(createdUnix, forKey: .createdUnix)
        try c.encode(device, forKey: .device); try c.encode(iosVersion, forKey: .iosVersion); try c.encode(appVersion, forKey: .appVersion)
        try c.encode(timeBase, forKey: .timeBase); try c.encode(uptimeToUnixOffset, forKey: .uptimeToUnixOffset)
        try c.encode(conventions, forKey: .conventions); try c.encode(video, forKey: .video)
        try c.encode(photo, forKey: .photo); try c.encode(imu, forKey: .imu)
        try c.encode(geo, forKey: .geo) // Encode explicit null when GPS is off/unavailable.
    }
}
