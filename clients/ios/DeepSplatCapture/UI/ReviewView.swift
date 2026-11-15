import SwiftUI
import ImageIO
import DeepSplatCore

struct ReviewView: View {
    @EnvironmentObject private var uploads: UploadQueue
    @Environment(\.dismiss) private var dismiss
    let id: UUID
    var onDone: (() -> Void)?
    @State private var manifest: CaptureManifest?
    @State private var image: UIImage?
    @State private var loading = true
    @State private var exporting = false
    @State private var share: SharedArchive?
    @State private var confirmDiscard = false
    @State private var loadError: String?
    private var record: UploadRecord? { uploads.records.first { $0.id == id } }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                VStack(alignment: .leading, spacing: 10) {
                    Eyebrow(text: record?.status == .uploaded ? "Capture delivered" : "Capture complete")
                    Text(manifest?.mapID ?? "Your inspection").font(.largeTitle.weight(.bold)).tracking(-1).fixedSize(horizontal: false, vertical: true)
                    if let manifest {
                        Text(Date(timeIntervalSince1970: manifest.createdUnix), format: .dateTime.month(.wide).day().hour().minute())
                            .font(.subheadline).foregroundStyle(Design.muted)
                    }
                }
                ZStack(alignment: .bottomLeading) {
                    RoundedRectangle(cornerRadius: 14).fill(Design.viewport)
                    if let image {
                        Image(uiImage: image).resizable().scaledToFit().frame(maxWidth: .infinity)
                            .frame(maxHeight: 380).accessibilityLabel("Crack photo, rotated for display only")
                    } else if loading {
                        ProgressView().tint(Design.blue).frame(height: 240).frame(maxWidth: .infinity)
                    } else {
                        Label("Photo preview unavailable", systemImage: "photo")
                            .font(.subheadline).foregroundStyle(Design.viewportMuted).frame(height: 240).frame(maxWidth: .infinity)
                    }
                    Label("Crack photo", systemImage: "camera")
                        .font(.caption.weight(.medium)).padding(.horizontal, 12).padding(.vertical, 9)
                        .foregroundStyle(Design.viewportInk)
                        .background(Design.viewportSurface, in: Capsule()).padding(14)
                }.clipShape(RoundedRectangle(cornerRadius: 14))
                    .overlay(RoundedRectangle(cornerRadius: 14).strokeBorder(Design.viewportLine, lineWidth: 1))
                if let manifest {
                    HStack(spacing: 18) {
                        Metric(value: "\(manifest.video.frames.count)", label: "Frames")
                        Metric(value: String(format: "%.1f s", manifest.video.durationS), label: "Duration")
                        Metric(value: "\(Int(manifest.imu.rateHz)) Hz", label: "Motion data")
                    }.captureCard()
                }
                if let record { deliveryCard(record) }
                if record?.status == .uploaded {
                    VStack(alignment: .leading, spacing: 12) {
                        Eyebrow(text: "Backend processing")
                        Label(processingTitle, systemImage: record?.backendStatus == .done ? "checkmark.circle" : "clock")
                            .font(.subheadline.weight(.semibold))
                        Text(record?.mock == true ? "Mock mode does not process or localize captures." : "Processing status is supplied by your backend. Pull to refresh.")
                            .font(.footnote).foregroundStyle(Design.muted)
                    }.captureCard()
                }
                if let manifest {
                    DisclosureGroup {
                        VStack(spacing: 16) {
                            detail("Crack photo", manifest.photo.highRes == true ? "High resolution" : "Camera frame fallback")
                            detail("Image size", "\(manifest.photo.intrinsics.width) × \(manifest.photo.intrinsics.height)")
                            detail("Frame sampling", "\(Int(manifest.video.fpsTarget)) fps")
                            detail("Accelerometer", "\(manifest.imu.sampleCounts["accel"] ?? 0) samples")
                            detail("Gyroscope", "\(manifest.imu.sampleCounts["gyro"] ?? 0) samples")
                            detail("Device motion", "\(manifest.imu.sampleCounts["motion"] ?? 0) samples")
                            detail("GPS hint", manifest.geo == nil ? "Not recorded" : "Included")
                        }.padding(.top, 18)
                    } label: {
                        Label("Capture details", systemImage: "doc.text").font(.subheadline.weight(.medium)).foregroundStyle(Design.ink)
                    }.tint(Design.muted).captureCard()
                }
                Label("Saved on this iPhone. Export a copy any time.", systemImage: "iphone")
                    .font(.caption).foregroundStyle(Design.muted).padding(.bottom, 8)
            }.padding(24)
        }
        .safeAreaInset(edge: .bottom) {
            VStack(spacing: 12) {
                if let record, record.status == .saved || record.status == .failed {
                    Button { uploads.enqueue(id) } label: {
                        HStack {
                            Text(record.status == .failed ? "Retry upload" : record.queued ? "Queued for upload" : "Upload capture")
                            Spacer(); Image(systemName: "arrow.up.right")
                        }
                    }.buttonStyle(PrimaryStyle()).disabled(record.queued && record.status == .saved)
                }
                Button {
                    exporting = true
                    Task {
                        defer { exporting = false }
                        do { share = SharedArchive(url: try await uploads.export(id)) }
                        catch { loadError = error.localizedDescription }
                    }
                } label: {
                    HStack(spacing: 8) {
                        if exporting { ProgressView().tint(Design.ink) }
                        Label(exporting ? "Preparing archive…" : "Export archive", systemImage: "square.and.arrow.up")
                    }
                }.buttonStyle(SecondaryStyle()).disabled(exporting || record?.status == .uploading)
            }
            .padding(.horizontal, 24).padding(.top, 16).padding(.bottom, 12)
            .background(Design.background)
            .overlay(alignment: .top) { Rectangle().fill(Design.line).frame(height: 1) }
        }
        .refreshable { await uploads.refreshBackendStatuses() }
        .background(Design.background).foregroundStyle(Design.ink)
        .navigationTitle("Review").navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(Design.background, for: .navigationBar)
        .toolbar {
            ToolbarItem(placement: .navigationBarTrailing) {
                HStack(spacing: 18) {
                    Button(role: .destructive) { confirmDiscard = true } label: { Image(systemName: "trash") }
                        .foregroundStyle(Design.muted).accessibilityLabel("Discard capture").disabled(exporting || record?.status == .uploading)
                    if let onDone { Button("Done", action: onDone).foregroundStyle(Design.blue) }
                }
            }
        }
        .task {
            defer { loading = false }
            uploads.refreshFiles()
            do {
                let folder = CapturePaths.folder(id)
                manifest = try await Task.detached { try CaptureWriter.readManifest(in: folder) }.value
                image = await Task.detached { () -> UIImage? in
                    guard let source = CGImageSourceCreateWithURL(folder.appendingPathComponent("photo.jpg") as CFURL, nil),
                          let image = CGImageSourceCreateThumbnailAtIndex(source, 0, [kCGImageSourceCreateThumbnailFromImageAlways: true, kCGImageSourceThumbnailMaxPixelSize: 1600] as CFDictionary) else { return nil }
                    return UIImage(cgImage: image, scale: 1, orientation: .right)
                }.value
            } catch { loadError = error.localizedDescription }
        }
        .confirmationDialog("Delete this capture and its local files?", isPresented: $confirmDiscard, titleVisibility: .visible) {
            Button("Discard capture", role: .destructive) {
                uploads.delete(id)
                if let onDone { onDone() } else { dismiss() }
            }
            Button("Cancel", role: .cancel) {}
        }
        .sheet(item: $share) { value in ShareSheet(url: value.url) }
        .alert("Capture", isPresented: Binding(get: { loadError != nil }, set: { if !$0 { loadError = nil } })) {
            Button("OK") { loadError = nil }
        } message: { Text(loadError ?? "") }
    }

    private var processingTitle: String {
        guard let record else { return "Awaiting upload" }
        if record.mock { return "Not available in mock mode" }
        switch record.backendStatus {
        case .done: return "Processing complete"
        case .failed: return "Processing failed"
        case .processing: return "Processing capture"
        default: return "Awaiting backend status"
        }
    }

    private func deliveryCard(_ record: UploadRecord) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .top, spacing: 12) {
                Image(systemName: record.status == .uploaded ? "checkmark.circle" : record.status == .failed ? "exclamationmark.circle" : "arrow.up.circle")
                    .font(.title2.weight(.light)).foregroundStyle(record.status == .uploaded ? Design.green : record.status == .failed ? Design.amber : Design.blue)
                VStack(alignment: .leading, spacing: 7) {
                    Text(uploads.statusText(record)).font(.subheadline.weight(.medium))
                    Text(record.mock ? (record.status == .uploaded ? "Archive copied to the local mock backend." : "Mock upload stays on this iPhone.") : record.status == .uploaded ? "Your original capture is still saved on this iPhone." : "Frames, photo and motion data travel together.")
                        .font(.footnote).foregroundStyle(Design.muted).lineSpacing(3)
                }
            }
            if record.status == .uploading { ProgressView(value: record.progress).tint(Design.blue) }
            if let error = record.error { Text(error).font(.footnote).foregroundStyle(Design.amber) }
        }.captureCard()
    }
    private func detail(_ title: String, _ value: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 16) {
            Text(title).foregroundStyle(Design.muted)
            Spacer()
            Text(value).foregroundStyle(Design.ink).multilineTextAlignment(.trailing)
        }.font(.footnote)
    }
}
private struct SharedArchive: Identifiable { let id = UUID(); let url: URL }
private struct ShareSheet: UIViewControllerRepresentable {
    let url: URL
    func makeUIViewController(context: Context) -> UIActivityViewController { UIActivityViewController(activityItems: [url], applicationActivities: nil) }
    func updateUIViewController(_ uiViewController: UIActivityViewController, context: Context) {}
}
