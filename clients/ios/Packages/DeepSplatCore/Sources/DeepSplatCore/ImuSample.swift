import Foundation

public struct Vector3: Codable, Equatable, Sendable {
    public let x: Double, y: Double, z: Double
    public init(_ x: Double, _ y: Double, _ z: Double) { self.x = x; self.y = y; self.z = z }
    public func scaled(_ factor: Double) -> Self { Self(x * factor, y * factor, z * factor) }
    public var values: [Double] { [x, y, z] }
}
public enum ImuSample {
    public static let standardGravity = 9.80665
    public static let accelHeader = "t,ax,ay,az\n"
    public static let gyroHeader = "t,gx,gy,gz\n"
    public static let motionHeader = "t,qx,qy,qz,qw,grav_x,grav_y,grav_z,uacc_x,uacc_y,uacc_z,rot_x,rot_y,rot_z\n"
    public static func specificForce(_ rawG: Vector3) -> Vector3 { rawG.scaled(-standardGravity) }
    public static func acceleration(_ rawG: Vector3) -> Vector3 { rawG.scaled(standardGravity) }
    public static func row(_ values: [Double]) -> String { values.map { String($0) }.joined(separator: ",") + "\n" }
}

/// Batches CSV writes. The caller confines an instance to one serial queue.
public final class CSVWriter {
    private let handle: FileHandle
    private var buffer = Data()
    private var pending = 0
    public private(set) var count = 0
    public init(url: URL, header: String) throws {
        try Data(header.utf8).write(to: url, options: .atomic)
        handle = try FileHandle(forWritingTo: url)
        try handle.seekToEnd()
    }
    public func append(_ values: [Double]) throws {
        guard values.allSatisfy(\.isFinite) else { throw CaptureError.invalid("IMU sample contains non-finite values.") }
        buffer.append(contentsOf: ImuSample.row(values).utf8)
        pending += 1; count += 1
        if pending >= 50 { try flush() }
    }
    public func flush() throws {
        guard !buffer.isEmpty else { return }
        try handle.write(contentsOf: buffer); buffer.removeAll(keepingCapacity: true); pending = 0
    }
    public func close() throws { try flush(); try handle.synchronize(); try handle.close() }
}
