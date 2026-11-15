import SwiftUI
import DeepSplatCore

struct CapturesView: View {
    @EnvironmentObject private var settings: AppSettings
    @EnvironmentObject private var uploads: UploadQueue
    @Environment(\.scenePhase) private var scenePhase
    @State private var recording = false
    @State private var showSettings = false
    @State private var filter = CaptureFilter.all
    @ScaledMetric(relativeTo: .largeTitle) private var headingSize = 32

    private var visibleRecords: [UploadRecord] {
        uploads.records.filter { filter == .all || (filter == .uploaded ? $0.status == .uploaded : $0.status != .uploaded) }
    }
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    VStack(alignment: .leading, spacing: 12) {
                        Eyebrow(text: "Field inspections")
                        Text(settings.configuration.mapID.isEmpty ? "Your inspections" : settings.configuration.mapID)
                            .font(.system(size: headingSize, weight: .bold)).tracking(-0.6)
                            .foregroundStyle(Design.ink).fixedSize(horizontal: false, vertical: true)
                        Text("Record the context. Keep the evidence.")
                            .font(.subheadline).foregroundStyle(Design.muted)
                    }.padding(.top, 8)
                    HStack(spacing: 16) {
                        Metric(value: "\(uploads.records.count)", label: "Captures")
                        Rectangle().fill(Design.line).frame(width: 1)
                        Metric(value: "\(uploads.records.filter { $0.status == .uploaded }.count)", label: "Uploaded")
                    }.fixedSize(horizontal: false, vertical: true).captureCard()
                    captureSetup
                    VStack(alignment: .leading, spacing: 18) {
                        HStack(alignment: .firstTextBaseline) {
                            Text("Recent captures").font(.title3.weight(.semibold))
                            Spacer()
                            Text(String(format: "%02d", uploads.records.count))
                                .font(.system(.caption, design: .monospaced)).foregroundStyle(Design.muted)
                        }
                        if !uploads.records.isEmpty {
                            ViewThatFits(in: .horizontal) {
                                filterBar
                                ScrollView(.horizontal, showsIndicators: false) { filterBar }
                            }
                        }
                        if visibleRecords.isEmpty {
                            VStack(alignment: .leading, spacing: 14) {
                                Image(systemName: "square.stack.3d.up").font(.system(size: 25, weight: .light)).foregroundStyle(Design.blue)
                                Text(uploads.records.isEmpty ? "Your first capture starts here." : "No captures in this view.")
                                    .font(.subheadline.weight(.medium))
                                Text(uploads.records.isEmpty ? "Record the surrounding area, then photograph the crack. Your captures will appear here." : "Try another filter to see your saved inspections.")
                                    .font(.footnote).foregroundStyle(Design.muted).lineSpacing(3)
                            }.captureCard()
                        }
                        ForEach(visibleRecords) { record in
                            NavigationLink { ReviewView(id: record.id) } label: {
                                CaptureRow(record: record, status: uploads.statusText(record))
                            }.buttonStyle(.plain)
                        }
                    }
                    HStack(spacing: 8) {
                        Image(systemName: settings.mockBackend ? "iphone" : "lock.shield")
                        Text(settings.mockBackend ? "Mock mode · captures stay on your iPhone" : "Captures upload to your configured backend")
                    }.font(.caption).foregroundStyle(Design.muted).padding(.bottom, 12)
                }.padding(.horizontal, 24).padding(.vertical, 16)
            }
            .background(Design.background).foregroundStyle(Design.ink)
            .navigationTitle("").navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(Design.background, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    Image("BrandWordmark").resizable().scaledToFit().frame(width: 116, height: 24)
                        .accessibilityLabel("deepsplat")
                }
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button { showSettings = true } label: {
                        Image(systemName: "slider.horizontal.3").font(.subheadline)
                            .frame(width: 42, height: 42).background(Design.surface, in: Circle())
                            .overlay(Circle().strokeBorder(Design.line, lineWidth: 1))
                    }.foregroundStyle(Design.ink).accessibilityLabel("Settings")
                }
            }
            .refreshable { uploads.refreshFiles(); await uploads.refreshBackendStatuses() }
            .sheet(isPresented: $showSettings) { SettingsView() }
            .fullScreenCover(isPresented: $recording, onDismiss: { uploads.refreshFiles() }) { RecordView(configuration: settings.configuration) }
            .task { uploads.refreshFiles() }
            .onChange(of: scenePhase) { phase in if phase == .active { uploads.refreshFiles(); uploads.kick() } }
            .alert("deepsplat", isPresented: Binding(get: { uploads.message != nil }, set: { if !$0 { uploads.message = nil } })) {
                Button("OK") { uploads.message = nil }
            } message: { Text(uploads.message ?? "") }
        }
    }
    private var filterBar: some View {
        HStack(spacing: 4) {
            ForEach(CaptureFilter.allCases) { value in
                Button { filter = value } label: {
                    Text(value.rawValue).font(.subheadline.weight(.medium))
                        .padding(.horizontal, 16).frame(minHeight: 44)
                        .foregroundStyle(filter == value ? Design.surface : Design.muted)
                        .background(filter == value ? Design.ink : Color.clear, in: Capsule())
                }.buttonStyle(.plain).accessibilityAddTraits(filter == value ? .isSelected : [])
            }
        }.padding(4).background(Design.elevated, in: Capsule())
    }

    private var captureSetup: some View {
        VStack(alignment: .leading, spacing: 20) {
            HStack(alignment: .top) {
                Image("BrandIcon").resizable().scaledToFit().frame(width: 48, height: 48).accessibilityHidden(true)
                Spacer()
                StatusPill(text: settings.mockBackend ? "On device" : "Cloud upload", symbol: settings.mockBackend ? "iphone" : "icloud")
            }
            VStack(alignment: .leading, spacing: 8) {
                Text("Start with the full picture").font(.title2.weight(.semibold)).tracking(-0.4)
                Text("Take a short sweep of the area, then a clear photo of the crack.")
                    .font(.subheadline).foregroundStyle(Design.muted).lineSpacing(4)
            }
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 12) { captureSteps }
                VStack(alignment: .leading, spacing: 12) { captureSteps }
            }
            Rectangle().fill(Design.line).frame(height: 1)
            Button { showSettings = true } label: {
                HStack(spacing: 12) {
                    Image(systemName: "square.3.layers.3d").foregroundStyle(Design.blue)
                    VStack(alignment: .leading, spacing: 4) {
                        Text(settings.configuration.mapID.isEmpty ? "Choose an inspection map" : settings.configuration.mapID)
                            .font(.subheadline.weight(.semibold)).foregroundStyle(Design.ink).lineLimit(2)
                        Text(settings.configuration.mapID.isEmpty ? "Add a map ID to begin" : "Inspection map · \(Int(settings.configuration.maxDuration)) s maximum")
                            .font(.caption).foregroundStyle(Design.muted)
                    }
                    Spacer(minLength: 0)
                    Image(systemName: "chevron.right").font(.caption.weight(.semibold)).foregroundStyle(Design.muted)
                }.frame(minHeight: 44)
            }.buttonStyle(.plain).accessibilityLabel("Change inspection map")
            Button {
                if settings.configuration.mapID.isEmpty { showSettings = true } else { recording = true }
            } label: {
                Label(settings.configuration.mapID.isEmpty ? "Set up capture" : "Record defect", systemImage: "camera")
            }.buttonStyle(PrimaryStyle())
        }.captureCard()
    }
    @ViewBuilder private var captureSteps: some View {
        CaptureStep(number: 1, title: "Sweep", active: true)
        CaptureStep(number: 2, title: "Photo")
        CaptureStep(number: 3, title: "Review")
    }

}

private enum CaptureFilter: String, CaseIterable, Identifiable {
    case all = "All", pending = "Pending", uploaded = "Uploaded"
    var id: Self { self }
}

private struct CaptureRow: View {
    let record: UploadRecord
    let status: String
    var body: some View {
        HStack(spacing: 14) {
            CaptureThumbnail(id: record.id).frame(width: 64, height: 64).clipShape(RoundedRectangle(cornerRadius: 9))
            VStack(alignment: .leading, spacing: 6) {
                Text(record.mapID).font(.subheadline.weight(.semibold)).foregroundStyle(Design.ink).lineLimit(1)
                Text(Date(timeIntervalSince1970: record.createdUnix), format: .dateTime.month(.abbreviated).day().hour().minute())
                    .font(.system(.caption, design: .monospaced)).foregroundStyle(Design.muted)
                HStack(spacing: 5) {
                    Circle().fill(record.status == .uploaded ? Design.green : record.status == .failed ? Design.amber : Design.blue).frame(width: 6, height: 6)
                    Text(status).font(.caption2).foregroundStyle(record.status == .failed ? Design.amber : Design.muted).lineLimit(2)
                }
            }
            Spacer(minLength: 0)
            Image(systemName: "arrow.up.right").font(.caption).foregroundStyle(Design.muted)
        }.padding(16).background(Design.surface, in: RoundedRectangle(cornerRadius: 14))
            .overlay(RoundedRectangle(cornerRadius: 14).strokeBorder(Design.line, lineWidth: 1))
    }
}
