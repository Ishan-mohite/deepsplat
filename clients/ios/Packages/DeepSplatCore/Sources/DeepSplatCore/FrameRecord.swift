import Foundation
import simd

public struct Intrinsics: Codable, Equatable, Sendable {
    public let fx: Float, fy: Float, cx: Float, cy: Float
    public let width: Int, height: Int
    public init(matrix: simd_float3x3, width: Int, height: Int) {
        fx = matrix[0][0]; fy = matrix[1][1]; cx = matrix[2][0]; cy = matrix[2][1]
        self.width = width; self.height = height
    }
}

public enum TrackingState: Equatable, Sendable {
    case normal, limited(String), notAvailable
    public var value: String {
        switch self {
        case .normal: return "normal"
        case .limited(let reason): return "limited:\(reason)"
        case .notAvailable: return "not_available"
        }
    }
}

public struct FrameRecord: Codable, Equatable, Sendable {
    public var file: String
    public let t: Double
    public let cameraToWorld: [Float]
    public let intrinsics: Intrinsics
    public let interfaceOrientation: String
    public let trackingState: String
    public let highRes: Bool?
    enum CodingKeys: String, CodingKey {
        case file, t, intrinsics
        case cameraToWorld = "camera_to_world", interfaceOrientation = "interface_orientation"
        case trackingState = "tracking_state", highRes = "high_res"
    }
    public init(file: String, timestamp: Double, transform: simd_float4x4, intrinsics: Intrinsics, tracking: TrackingState, highRes: Bool? = nil) {
        self.file = file; t = timestamp; cameraToWorld = Self.rowMajor(transform)
        self.intrinsics = intrinsics; interfaceOrientation = "portrait"
        trackingState = tracking.value; self.highRes = highRes
    }
    public static func rowMajor(_ matrix: simd_float4x4) -> [Float] {
        (0..<4).flatMap { row in (0..<4).map { column in matrix[column][row] } }
    }
    public static func matrix(fromRowMajor values: [Float]) throws -> simd_float4x4 {
        guard values.count == 16, values.allSatisfy(\.isFinite) else { throw CaptureError.invalid("A pose must contain 16 finite values.") }
        var matrix = matrix_identity_float4x4
        for row in 0..<4 { for column in 0..<4 { matrix[column][row] = values[row * 4 + column] } }
        return matrix
    }
}

public enum CaptureError: LocalizedError {
    case invalid(String)
    public var errorDescription: String? { if case .invalid(let message) = self { return message }; return nil }
}
