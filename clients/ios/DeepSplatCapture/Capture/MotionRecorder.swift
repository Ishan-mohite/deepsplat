import CoreMotion
import DeepSplatCore

/// All sensor callbacks and file writes are confined to one operation queue.
final class MotionRecorder {
    private let manager = CMMotionManager()
    private let queue: OperationQueue = {
        let value = OperationQueue(); value.name = "DeepSplat.IMU"; value.maxConcurrentOperationCount = 1
        value.qualityOfService = .userInitiated; return value
    }()
    private var accel: CSVWriter?, gyro: CSVWriter?, motion: CSVWriter?
    private var firstError: Error?
    private var startTime: Double = 0

    func start(folder: URL, rate: Double, startTime: Double) throws {
        guard manager.isAccelerometerAvailable, manager.isGyroAvailable, manager.isDeviceMotionAvailable,
              CMMotionManager.availableAttitudeReferenceFrames().contains(.xArbitraryCorrectedZVertical) else {
            throw CaptureError.invalid("This iPhone does not provide the required motion sensors/reference frame.")
        }
        self.startTime = startTime; firstError = nil
        accel = try CSVWriter(url: folder.appendingPathComponent("imu/accel.csv"), header: ImuSample.accelHeader)
        gyro = try CSVWriter(url: folder.appendingPathComponent("imu/gyro.csv"), header: ImuSample.gyroHeader)
        motion = try CSVWriter(url: folder.appendingPathComponent("imu/motion.csv"), header: ImuSample.motionHeader)
        manager.accelerometerUpdateInterval = 1 / rate; manager.gyroUpdateInterval = 1 / rate; manager.deviceMotionUpdateInterval = 1 / rate
        manager.startAccelerometerUpdates(to: queue) { [weak self] sample, error in
            self?.write(error: error) {
                guard let self, let sample, sample.timestamp >= self.startTime else { return }
                let a = ImuSample.specificForce(Vector3(sample.acceleration.x, sample.acceleration.y, sample.acceleration.z))
                try self.accel?.append([sample.timestamp] + a.values)
            }
        }
        manager.startGyroUpdates(to: queue) { [weak self] sample, error in
            self?.write(error: error) {
                guard let self, let sample, sample.timestamp >= self.startTime else { return }
                try self.gyro?.append([sample.timestamp, sample.rotationRate.x, sample.rotationRate.y, sample.rotationRate.z])
            }
        }
        manager.startDeviceMotionUpdates(using: .xArbitraryCorrectedZVertical, to: queue) { [weak self] sample, error in
            self?.write(error: error) {
                guard let self, let sample, sample.timestamp >= self.startTime else { return }
                let q = sample.attitude.quaternion
                let g = ImuSample.acceleration(Vector3(sample.gravity.x, sample.gravity.y, sample.gravity.z))
                let a = ImuSample.acceleration(Vector3(sample.userAcceleration.x, sample.userAcceleration.y, sample.userAcceleration.z))
                try self.motion?.append([sample.timestamp, q.x, q.y, q.z, q.w] + g.values + a.values + [sample.rotationRate.x, sample.rotationRate.y, sample.rotationRate.z])
            }
        }
    }
    private func write(error: Error?, body: () throws -> Void) {
        if let error { firstError = firstError ?? error; return }
        do { try body() } catch { firstError = firstError ?? error }
    }
    func stop(completion: @escaping ([String: Int], Error?) -> Void) {
        manager.stopAccelerometerUpdates(); manager.stopGyroUpdates(); manager.stopDeviceMotionUpdates()
        queue.addOperation { [self] in
            let counts = ["accel": accel?.count ?? 0, "gyro": gyro?.count ?? 0, "motion": motion?.count ?? 0]
            for file in [accel, gyro, motion] { do { try file?.close() } catch { firstError = firstError ?? error } }
            accel = nil; gyro = nil; motion = nil
            completion(counts, firstError)
        }
    }
}
