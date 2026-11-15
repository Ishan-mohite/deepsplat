import Foundation

public struct FrameSampler: Sendable {
    public let fps: Double
    public let sourceFPS: Double
    public private(set) var lastKept: Double?
    public init(fps: Double = 5, sourceFPS: Double = 60) {
        self.fps = max(1, min(fps, 30)); self.sourceFPS = max(self.fps, sourceFPS)
    }
    public mutating func shouldKeep(_ timestamp: Double) -> Bool {
        guard timestamp.isFinite else { return false }
        if let lastKept, timestamp < lastKept + 1 / fps - 0.5 / sourceFPS { return false }
        lastKept = timestamp
        return true
    }
    public mutating func reset() { lastKept = nil }
}
