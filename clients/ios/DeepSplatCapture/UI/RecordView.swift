import SwiftUI

struct RecordView: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(\.scenePhase) private var scenePhase
    @StateObject private var controller: CaptureController
    @State private var confirmDiscard = false
    init(configuration: CaptureConfiguration) { _controller = StateObject(wrappedValue: CaptureController(configuration: configuration)) }

    var body: some View {
        NavigationStack {
            if let id = controller.savedID {
                ReviewView(id: id, onDone: { dismiss() })
            } else {
                ZStack {
                    ARViewContainer(controller: controller.ar).ignoresSafeArea()
                    LinearGradient(stops: [.init(color: .black.opacity(0.65), location: 0), .init(color: .clear, location: 0.35), .init(color: .clear, location: 0.5), .init(color: .black.opacity(0.8), location: 1)], startPoint: .top, endPoint: .bottom)
                        .ignoresSafeArea().allowsHitTesting(false)
                    PickReticle().stroke(Design.pick, style: StrokeStyle(lineWidth: 2, lineCap: .round))
                        .frame(width: 120, height: 120).offset(y: -70).accessibilityHidden(true).allowsHitTesting(false)
                    VStack(spacing: 18) {
                        header
                        if controller.trackingWarning {
                            Label("Tracking limited. Move slowly towards a textured area.", systemImage: "exclamationmark.triangle")
                                .font(.footnote).foregroundStyle(Design.amberSoft).padding(14)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .background(.black.opacity(0.7), in: RoundedRectangle(cornerRadius: 14))
                        }
                        Spacer(minLength: 16)
                        captureControls
                    }.padding(.horizontal, 24).padding(.vertical, 16)
                }
                .foregroundStyle(Design.viewportInk)
                .toolbar(.hidden, for: .navigationBar)
                .task { await controller.openCamera() }
            }
        }
        .preferredColorScheme(.light)
        .interactiveDismissDisabled(controller.state == .recording)
        .confirmationDialog("Discard this capture?", isPresented: $confirmDiscard, titleVisibility: .visible) {
            Button("Discard capture", role: .destructive) { controller.close(discard: true); dismiss() }
            Button(controller.state == .recording ? "Keep recording" : "Cancel", role: .cancel) {}
        }
        .alert("Capture", isPresented: Binding(get: { controller.message != nil }, set: { if !$0 { controller.message = nil } })) {
            Button("OK") { controller.message = nil }
        } message: { Text(controller.message ?? "") }
        .onChange(of: scenePhase) { phase in if phase == .background { controller.interrupt("Capture stopped when the app moved to the background.") } }
        .onDisappear { controller.close(discard: false) }
    }

    private var header: some View {
        VStack(spacing: 16) {
            HStack(spacing: 14) {
                Button {
                    if controller.state == .recording || controller.state == .stopped { confirmDiscard = true }
                    else { dismiss() }
                } label: {
                    Image(systemName: "xmark").font(.subheadline.weight(.medium)).frame(width: 44, height: 44)
                        .background(.black.opacity(0.4), in: Circle()).overlay(Circle().strokeBorder(Design.viewportInk.opacity(0.2), lineWidth: 1))
                }.accessibilityLabel("Close capture")
                VStack(alignment: .leading, spacing: 4) {
                    Text("INSPECTION CAPTURE").font(.system(.caption2, design: .monospaced)).tracking(1.4).foregroundStyle(Design.viewportInk.opacity(0.65))
                    Text(controller.configuration.mapID).font(.subheadline.weight(.medium)).lineLimit(1)
                }
                Spacer(minLength: 0)
                if controller.state == .recording {
                    HStack(spacing: 6) {
                        Circle().fill(Design.rust).frame(width: 6, height: 6)
                        Text("REC").font(.system(.caption2, design: .monospaced).weight(.medium))
                    }.padding(10).background(.black.opacity(0.4), in: Capsule()).accessibilityLabel("Recording")
                }
            }
            HStack {
                StatusPill(text: trackingTitle, color: controller.tracking == "normal" ? Design.greenSoft : Design.amberSoft)
                    .background(.black.opacity(0.4), in: Capsule())
                Spacer()
                if controller.hasPhoto {
                    Label("Photo saved", systemImage: "checkmark.circle.fill").font(.caption).foregroundStyle(Design.greenSoft)
                }
            }
        }
    }

    private var captureControls: some View {
        VStack(spacing: 22) {
            HStack(alignment: .lastTextBaseline) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(String(format: "%02d:%02d", Int(controller.elapsed) / 60, Int(controller.elapsed) % 60))
                        .font(.system(.largeTitle, design: .monospaced).weight(.semibold)).tracking(-1)
                    Text("of \(Int(controller.configuration.maxDuration)) seconds").font(.caption).foregroundStyle(Design.muted)
                }.accessibilityElement(children: .combine)
                Spacer()
                VStack(alignment: .trailing, spacing: 6) {
                    Text("\(controller.frameCount)").font(.title2.monospacedDigit().weight(.medium))
                    Text("frames saved").font(.caption).foregroundStyle(Design.muted)
                }.accessibilityElement(children: .combine)
            }
            VStack(spacing: 14) {
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 14) { workflowSteps }
                    VStack(alignment: .leading, spacing: 8) { workflowSteps }
                }.frame(maxWidth: .infinity, alignment: .leading)
                Text(instruction).font(.subheadline).lineSpacing(3).frame(maxWidth: .infinity, alignment: .leading)
                    .foregroundStyle(Design.muted).fixedSize(horizontal: false, vertical: true)
                if controller.state == .idle {
                    Button { controller.start() } label: {
                        HStack { Image(systemName: "record.circle"); Text("Start recording"); Spacer(); Image(systemName: "arrow.right") }
                    }.buttonStyle(PrimaryStyle()).disabled(controller.tracking != "normal")
                } else if controller.state == .recording {
                    HStack(spacing: 20) {
                        Button { controller.takePhoto() } label: {
                            HStack(spacing: 10) {
                                ZStack {
                                    Circle().fill(Design.ink).frame(width: 48, height: 48)
                                    if controller.takingPhoto { ProgressView().tint(Design.surface) }
                                    else { Image(systemName: "camera.fill").foregroundStyle(Design.surface).font(.title3) }
                                }
                                Text(controller.takingPhoto ? "Capturing…" : controller.hasPhoto ? "Retake photo" : "Crack photo")
                                    .font(.subheadline.weight(.medium)).foregroundStyle(Design.ink)
                            }.frame(maxWidth: .infinity, alignment: .leading).padding(.vertical, 6)
                        }.buttonStyle(.plain).disabled(controller.takingPhoto)
                        Button { controller.stop() } label: { Label("Stop", systemImage: "stop.fill") }
                            .buttonStyle(SecondaryStyle()).frame(maxWidth: 120).disabled(!controller.hasPhoto || controller.takingPhoto)
                    }
                } else if controller.saveError != nil {
                    Label("Capture could not be finalized", systemImage: "exclamationmark.circle").font(.subheadline).foregroundStyle(Design.amber)
                    Button("Close") { dismiss() }.buttonStyle(SecondaryStyle())
                } else {
                    ProgressView("Saving your capture…").tint(Design.blue).font(.subheadline).padding(.vertical, 12)
                }
            }
        }
        .foregroundStyle(Design.ink).padding(20)
        .background(Design.surface, in: RoundedRectangle(cornerRadius: 22))
        .overlay(RoundedRectangle(cornerRadius: 22).strokeBorder(Design.line, lineWidth: 1))
    }
    @ViewBuilder private var workflowSteps: some View {
        CaptureStep(number: 1, title: "Sweep", complete: controller.hasPhoto, active: !controller.hasPhoto)
        CaptureStep(number: 2, title: "Crack photo", complete: controller.hasPhoto, active: controller.state == .recording && !controller.hasPhoto)
        CaptureStep(number: 3, title: "Review", active: controller.state == .stopped)
    }
    private var instruction: String {
        if controller.state == .idle { return "Start a few metres away. Move slowly towards the crack as you record." }
        if controller.state == .stopped { return controller.saveError == nil ? "Keeping your frames, photo and motion data together." : "Your available files are preserved in Files." }
        return controller.hasPhoto ? "Photo saved. Keep sweeping, or tap Stop to review." : "Take a clear crack photo to enable Stop."
    }
    private var trackingTitle: String {
        if controller.tracking == "normal" { return "Tracking ready" }
        return controller.tracking.replacingOccurrences(of: "limited:", with: "").replacingOccurrences(of: "_", with: " ").capitalized
    }
}

