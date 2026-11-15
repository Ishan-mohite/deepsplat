import ARKit
import DeepSplatCore

final class ARSessionController: NSObject, ARSessionDelegate {
    let session = ARSession()
    let queue = DispatchQueue(label: "DeepSplat.Capture", qos: .userInitiated)
    private(set) var sourceFPS: Double = 60
    private(set) var supportsHighResolution = false
    var onFrame: ((ARFrame) -> Void)?
    var onTracking: ((TrackingState) -> Void)?
    var onFailure: ((String) -> Void)?

    override init() { super.init(); session.delegate = self; session.delegateQueue = queue }
    func run() throws {
        guard ARWorldTrackingConfiguration.isSupported else { throw CaptureError.invalid("ARKit world tracking requires a supported physical iPhone.") }
        let configuration = ARWorldTrackingConfiguration()
        configuration.isAutoFocusEnabled = true
        if let format = ARWorldTrackingConfiguration.recommendedVideoFormatForHighResolutionFrameCapturing {
            configuration.videoFormat = format; supportsHighResolution = true
        }
        sourceFPS = Double(configuration.videoFormat.framesPerSecond)
        session.run(configuration, options: [.resetTracking, .removeExistingAnchors])
    }
    func pause() { session.pause() }
    func session(_ session: ARSession, didUpdate frame: ARFrame) { onFrame?(frame) }
    func session(_ session: ARSession, cameraDidChangeTrackingState camera: ARCamera) { onTracking?(Self.tracking(camera.trackingState)) }
    func session(_ session: ARSession, didFailWithError error: Error) { onFailure?(error.localizedDescription) }
    func sessionWasInterrupted(_ session: ARSession) { onFailure?("The AR session was interrupted. Recording has stopped to preserve its coordinate frame.") }
    func sessionInterruptionEnded(_ session: ARSession) { onTracking?(.notAvailable) }
    static func tracking(_ state: ARCamera.TrackingState) -> TrackingState {
        switch state {
        case .normal: return .normal
        case .notAvailable: return .notAvailable
        case .limited(let reason):
            switch reason {
            case .initializing: return .limited("initializing")
            case .excessiveMotion: return .limited("excessive_motion")
            case .insufficientFeatures: return .limited("insufficient_features")
            case .relocalizing: return .limited("relocalizing")
            @unknown default: return .limited("unknown")
            }
        }
    }
}
