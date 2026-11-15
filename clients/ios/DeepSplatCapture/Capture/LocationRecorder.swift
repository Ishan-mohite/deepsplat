import CoreLocation
import DeepSplatCore

@MainActor
final class LocationRecorder: NSObject, @preconcurrency CLLocationManagerDelegate {
    private let manager = CLLocationManager()
    private var continuation: CheckedContinuation<GeoHint?, Never>?
    private var requestedAt = Date()
    override init() { super.init(); manager.delegate = self; manager.desiredAccuracy = kCLLocationAccuracyBest }
    func fix() async -> GeoHint? {
        guard continuation == nil else { return nil }
        requestedAt = Date()
        return await withCheckedContinuation { continuation in
            self.continuation = continuation
            if manager.authorizationStatus == .notDetermined { manager.requestWhenInUseAuthorization() }
            else { requestIfAllowed() }
            Task { [weak self] in
                try? await Task.sleep(nanoseconds: 5_000_000_000)
                self?.finish(nil)
            }
        }
    }
    func cancel() { finish(nil) }
    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) { if continuation != nil { requestIfAllowed() } }
    private func requestIfAllowed() {
        switch manager.authorizationStatus {
        case .authorizedWhenInUse, .authorizedAlways: manager.requestLocation()
        case .denied, .restricted: finish(nil)
        default: break
        }
    }
    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let location = locations.last, location.horizontalAccuracy >= 0,
              abs(location.timestamp.timeIntervalSince(requestedAt)) <= 5 else { return }
        // CLLocation exposes WGS84 ellipsoidal altitude on every supported deployment version.
        // Both altitude values require valid vertical accuracy; never substitute MSL for ellipsoidal.
        let ellipsoidal: Double? = location.verticalAccuracy > 0 ? location.ellipsoidalAltitude : nil
        let msl: Double? = location.verticalAccuracy > 0 ? location.altitude : nil
        finish(GeoHint(lat: location.coordinate.latitude, lon: location.coordinate.longitude,
                       ellipsoidal: ellipsoidal, msl: msl,
                       horizontalAccuracy: location.horizontalAccuracy, verticalAccuracy: location.verticalAccuracy,
                       unix: location.timestamp.timeIntervalSince1970))
    }
    func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) { finish(nil) }
    private func finish(_ value: GeoHint?) {
        guard let continuation else { return }
        self.continuation = nil; manager.stopUpdatingLocation(); continuation.resume(returning: value)
    }
}
