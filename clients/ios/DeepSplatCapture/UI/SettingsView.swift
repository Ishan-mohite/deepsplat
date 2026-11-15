import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var settings: AppSettings
    @EnvironmentObject private var uploads: UploadQueue
    @Environment(\.dismiss) private var dismiss
    @State private var token = ""
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
                        SecureField("Enter backend token", text: $token).textInputAutocapitalization(.never).autocorrectionDisabled()
                            .font(.subheadline).onChange(of: token) { settings.setToken($0) }
                    }.padding(.vertical, 5)
                    if !settings.backendURL.isEmpty, BackendClient.baseURL(settings.backendURL) == nil {
                        Label("Use an HTTPS URL without credentials or query parameters.", systemImage: "exclamationmark.circle")
                            .font(.caption).foregroundStyle(Design.amber)
                    }
                    if let error = settings.tokenError { Text(error).font(.caption).foregroundStyle(Design.amber) }
                } header: { Eyebrow(text: "Backend connection") } footer: {
                    Label("Token stored securely in the iPhone Keychain.", systemImage: "lock")
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
            .onAppear { token = settings.token() }
            .onChange(of: settings.backendURL) { _ in token = settings.token() }
        }.preferredColorScheme(.light)
    }
    private func settingValue(_ title: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(title).font(.subheadline)
            Text(value).font(.system(.caption, design: .monospaced)).foregroundStyle(Design.blue)
        }.padding(.vertical, 3)
    }
}
