import SwiftUI
import RealityKit

struct ARViewContainer: UIViewRepresentable {
    let controller: ARSessionController
    func makeUIView(context: Context) -> ARView {
        let view = ARView(frame: .zero, cameraMode: .ar, automaticallyConfigureSession: false)
        view.session = controller.session
        // Camera preview only: no anchors, image transforms, or client-side localization.
        return view
    }
    func updateUIView(_ uiView: ARView, context: Context) {}
}
