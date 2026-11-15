import SwiftUI
import UIKit

struct SettingsView: View {
    @EnvironmentObject private var settings: AppSettings
    @EnvironmentObject private var uploads: UploadQueue
    @Environment(\.dismiss) private var dismiss
    @State private var showToken = false
    @State private var showAdvanced = false

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    VStack(alignment: .leading, spacing: 14) {
                        HStack {
                            Image(systemName: "viewfinder").font(.title2.weight(.light)).foregroundStyle(Design.blue)
                            Spacer()
                            StatusPill(text: settings.mockBackend ? "On device" : "Cloud upload")
                        }
                        Text("Ready for the field.").font(.title2.weight(.medium)).tracking(-0.5)
                        Text("Choose your map and where captures go.\nYou can record without a network connection.")
                            .font(.footnote).foregroundStyle(Design.muted).lineSpacing(3)
                    }.padding(.vertical, 8)
                }.listRowBackground(Design.surface)
                Section {
                    HStack(spacing: 12) {
                        Image(systemName: "square.3.layers.3d").foregroundStyle(Design.blue)
                        TextField("Enter map ID", text: $settings.mapID)
                            .textInputAutocapitalization(.never).autocorrectionDisabled().accessibilityLabel("Map ID")
                    }
                } header: { Eyebrow(text: "Inspection map") } footer: {
                    Text("Use the map ID provided by your backend team.").foregroundStyle(Design.muted)
                }.listRowBackground(Design.surface)
                Section {
                    Toggle(isOn: $settings.mockBackend) {
                        Label("Mock backend", systemImage: "iphone").foregroundStyle(Design.ink)
                    }
                    Toggle(isOn: $settings.wifiOnly) {
                        Label("Upload on Wi-Fi only", systemImage: "wifi").foregroundStyle(Design.ink)
                    }
                } header: { Eyebrow(text: "Delivery") } footer: {
                    Text(settings.mockBackend ? "Mock mode keeps archives on this iPhone, in Files → DeepSplat → mock-uploads." : "Captures upload in the background when a connection is available.")
                        .foregroundStyle(Design.muted)
                }.listRowBackground(Design.surface)
                Section {
                    VStack(alignment: .leading, spacing: 10) {
                        Text("Backend URL").font(.caption).foregroundStyle(Design.muted)
                        TextField("https://backend.example.com", text: $settings.backendURL)
                            .keyboardType(.URL).textContentType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                            .font(.subheadline).accessibilityLabel("Backend URL")
                    }.padding(.vertical, 5)
                    VStack(alignment: .leading, spacing: 10) {
                        Text("API token").font(.caption).foregroundStyle(Design.muted)
                        HStack(spacing: 12) {
                            SessionTokenField(text: tokenBinding, isVisible: showToken)
                                .frame(minHeight: 44).privacySensitive()
                            Button {
                                showToken.toggle()
                            } label: {
                                Label(showToken ? "Hide" : "Show", systemImage: showToken ? "eye.slash" : "eye")
                                    .font(.caption.weight(.semibold)).frame(minHeight: 44)
                            }
                            .buttonStyle(.borderless)
                            .accessibilityLabel(showToken ? "Hide API token" : "Show API token")
                        }
                    }.padding(.vertical, 5)
                    if !settings.backendURL.isEmpty, BackendClient.baseURL(settings.backendURL) == nil {
                        Label("Use an HTTPS URL without credentials or query parameters.", systemImage: "exclamationmark.circle")
                            .font(.caption).foregroundStyle(Design.amber)
                    }
                } header: { Eyebrow(text: "Backend connection") } footer: {
                    Label("Edit the token at any time. Changes apply to new requests immediately and stay for this app session.", systemImage: "lock")
                        .foregroundStyle(Design.muted)
                }.listRowBackground(Design.surface)
                Section {
                    Stepper(value: $settings.maxDuration, in: 5...60, step: 5) {
                        settingValue("Maximum duration", "\(Int(settings.maxDuration)) sec")
                    }
                    Toggle("Include GPS hint", isOn: $settings.recordGPS)
                } header: { Eyebrow(text: "Recording") } footer: {
                    Text("GPS is optional and is requested only when you record with this switch enabled.").foregroundStyle(Design.muted)
                }.listRowBackground(Design.surface)
                Section {
                    DisclosureGroup(isExpanded: $showAdvanced) {
                        Stepper(value: $settings.fps, in: 1...15) { settingValue("Frame sampling", "\(Int(settings.fps)) fps") }.padding(.top, 12)
                        Stepper(value: $settings.imuRateHz, in: 25...100, step: 25) { settingValue("Motion sampling", "\(Int(settings.imuRateHz)) Hz") }
                        VStack(alignment: .leading, spacing: 12) {
                            settingValue("Frame JPEG quality", "\(Int(settings.jpegQuality * 100))%")
                            Slider(value: $settings.jpegQuality, in: 0.5...1, step: 0.05).accessibilityLabel("Frame JPEG quality")
                        }.padding(.vertical, 10)
                        Text("Crack photos use 95% JPEG quality. Original image orientation and camera metadata are preserved.")
                            .font(.caption).foregroundStyle(Design.muted).padding(.vertical, 8)
                    } label: {
                        Label("Advanced recording", systemImage: "slider.horizontal.3").font(.subheadline)
                    }
                }.listRowBackground(Design.surface)
                Section {
                    HStack { Spacer(); Text("deepsplat · capture").font(.system(.caption2, design: .monospaced)).tracking(1.5).foregroundStyle(Design.muted); Spacer() }
                }.listRowBackground(Color.clear)
            }
            .scrollContentBackground(.hidden).background(Design.background).foregroundStyle(Design.ink).tint(Design.blue)
            .navigationTitle("Settings").navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(Design.background, for: .navigationBar)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { uploads.kick(); dismiss() }.foregroundStyle(Design.blue) } }
            .onChange(of: settings.backendURL) { _ in showToken = false }
        }.preferredColorScheme(.light)
    }
    private var tokenBinding: Binding<String> {
        Binding(get: { settings.token() }, set: { settings.setToken($0) })
    }
    private func settingValue(_ title: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(title).font(.subheadline)
            Text(value).font(.system(.caption, design: .monospaced)).foregroundStyle(Design.blue)
        }.padding(.vertical, 3)
    }
}

/// Keep one UIKit field alive while masking changes. Replacing SecureField with
/// TextField can lose the selection, and secure fields can clear on refocus.
private struct SessionTokenField: UIViewRepresentable {
    @Binding var text: String
    let isVisible: Bool

    func makeCoordinator() -> Coordinator { Coordinator(text: $text) }

    func makeUIView(context: Context) -> UITextField {
        let field = UITextField()
        field.placeholder = "Enter backend token"
        field.accessibilityLabel = "API token"
        field.font = .preferredFont(forTextStyle: .subheadline)
        field.adjustsFontForContentSizeCategory = true
        field.textColor = UIColor(Design.ink)
        field.tintColor = UIColor(Design.blue)
        field.keyboardType = .asciiCapable
        field.autocapitalizationType = .none
        field.autocorrectionType = .no
        field.spellCheckingType = .no
        field.smartQuotesType = .no
        field.smartDashesType = .no
        field.textContentType = nil
        field.clearsOnBeginEditing = false
        field.clearButtonMode = .whileEditing
        field.returnKeyType = .done
        field.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        field.delegate = context.coordinator
        field.addTarget(context.coordinator, action: #selector(Coordinator.changed(_:)), for: .editingChanged)
        return field
    }

    func updateUIView(_ field: UITextField, context: Context) {
        context.coordinator.text = $text
        let selection = field.selectedTextRange.map {
            (field.offset(from: field.beginningOfDocument, to: $0.start),
             field.offset(from: field.beginningOfDocument, to: $0.end))
        }
        let changedVisibility = field.isSecureTextEntry == isVisible
        if changedVisibility { field.isSecureTextEntry = !isVisible }
        // Avoid assigning text on each keystroke: that moves the cursor to the end.
        if field.text != text || changedVisibility { field.text = text }
        if changedVisibility, let (start, end) = selection,
           let from = field.position(from: field.beginningOfDocument, offset: start),
           let to = field.position(from: field.beginningOfDocument, offset: end) {
            field.selectedTextRange = field.textRange(from: from, to: to)
        }
    }

    final class Coordinator: NSObject, UITextFieldDelegate {
        var text: Binding<String>
        init(text: Binding<String>) { self.text = text }
        @objc func changed(_ field: UITextField) { text.wrappedValue = field.text ?? "" }
        func textFieldShouldReturn(_ field: UITextField) -> Bool {
            field.resignFirstResponder()
            return true
        }
    }
}
