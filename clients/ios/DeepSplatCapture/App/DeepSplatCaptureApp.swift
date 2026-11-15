import SwiftUI

@main
struct DeepSplatCaptureApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var settings: AppSettings
    @StateObject private var uploads: UploadQueue
    init() {
        let settings = AppSettings()
        _settings = StateObject(wrappedValue: settings)
        _uploads = StateObject(wrappedValue: UploadQueue(settings: settings))
    }
    var body: some Scene {
        WindowGroup {
            CapturesView().environmentObject(settings).environmentObject(uploads)
                .tint(Design.blue).preferredColorScheme(.light)
        }
    }
}

final class AppDelegate: NSObject, UIApplicationDelegate {
    func application(_ application: UIApplication, handleEventsForBackgroundURLSession identifier: String, completionHandler: @escaping () -> Void) {
        Task { @MainActor in BackgroundEvents.attach(identifier, handler: completionHandler) }
    }
}

