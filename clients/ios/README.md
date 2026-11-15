# DeepSplat Capture

Native iPhone recording client implementing [`docs/PLAN.md`](../../docs/PLAN.md). It records raw ARKit frames and camera metadata, synchronized IMU, one crack photo and optional GPS. It exports a ZIP and uploads it to a backend-issued Google Cloud Storage signed URL. All localization and damage processing stay on the backend; the app does not display pose results.

## Build and install

Requirements: Xcode 16+, XcodeGen, and an ARKit world-tracking-capable iPhone with iOS 16+. No third-party app/package dependencies. The core package uses only Foundation and simd.

```sh
brew install xcodegen
cd clients/ios
cp Local.xcconfig.example Local.xcconfig
# Edit Local.xcconfig: DEVELOPMENT_TEAM and a unique PRODUCT_BUNDLE_IDENTIFIER.
xcodegen generate
open DeepSplatCapture.xcodeproj
```

If `Local.xcconfig` already exists, keep it rather than overwriting your signing values. The generated project and local signing file are git-ignored; `project.yml` is the source of truth. XcodeGen also regenerates `DeepSplatCapture/Info.plist` from the spec.

Add your Apple account in Xcode Settings > Accounts, enable automatic signing, connect and trust your iPhone, enable Developer Mode, select that iPhone, then press Run. With a free Personal Team, reinstall from Xcode after the seven-day provisioning expiration. If prompted, trust the developer under iPhone Settings > General > VPN & Device Management. No TestFlight or CI setup is included.

Unsigned device build (for compile verification, not installation):

```sh
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild \
  -project DeepSplatCapture.xcodeproj -scheme DeepSplatCapture \
  -destination 'generic/platform=iOS' -derivedDataPath /tmp/deepsplat-build \
  CODE_SIGNING_ALLOWED=NO build
```

For a signed build, select your connected iPhone in Xcode or use the destination command in the plan. The app's deployment target is iOS 16; the optional XCTest host targets iOS 17 because the current Xcode's XCTest framework requires it.

## Interface

The interface takes its dark surfaces, warm white typography, violet accents and fine borders from the [MultiSet developer page](https://multiset.ai/developers), while keeping DeepSplat's own identity and capture workflow. The spatial grid is decorative, not a localization result.

- Home shows the configured map, recording rates, photo thumbnails and All / Pending / Uploaded filters.
- The camera keeps the viewfinder visible, labels tracking separately from recording, and guides Sweep → Crack photo → Review. Stop remains disabled until a crack photo is saved.
- Review leads with the photo, counts and real delivery state; additional sensor details expand on demand. Upload and Export stay at the bottom.
- Settings groups map, delivery, credentials and recording; less common sampling controls are under Advanced recording.
- A locally drawn, opaque 1024-pixel app icon is included in the asset catalog. Recreate it with `Scripts/generate_app_icon.swift` on a Mac.

The app uses native system fonts and symbols, without bundling fonts or artwork from MultiSet. Refined screens and the icon compile in the signed iPhone build; on-device visual inspection is still required. Your selected team and bundle ID are preserved in `Local.xcconfig` when regenerating this project.

## Record and review

1. Open Settings and enter a map ID. Mock backend is on initially, and GPS is off.
2. Tap **Record defect**. The preview starts world tracking; **Start recording** becomes available when tracking is normal.
3. Start a few metres away and move slowly towards the damage. Default recording settings are 5 FPS, 100 Hz IMU, 20 seconds, 90% frame JPEG quality.
4. Tap **Crack photo**, then **Stop**. Retaking replaces the crack photo while frames and motion continue. High-resolution capture uses ARKit's recommended format; unsupported or failed capture falls back to the current camera frame and logs why.
5. Review the photo and counts. **Upload capture** queues it; **Export archive** opens the share sheet for AirDrop/Files; the trash button discards local files.

The UI stays portrait. Images are saved in sensor orientation without an EXIF orientation tag, rotation, mirroring or pose conversion. Only the review preview rotates clockwise for display. Frames carry the actual ARKit image resolution/intrinsics and unchanged camera-to-world pose serialized row-major. Each frame's tracking quality is retained; a warning appears after two seconds of limited tracking.

Frames and IMU share system-uptime seconds. The manifest records the Unix offset measured at recording start, device identifier, version, conventions, counts, capture/map IDs and optional GPS. Accelerometer specific force is `-rawG * 9.80665`; motion gravity/user acceleration keep CoreMotion signs and are scaled to SI units. Gyro rates are radians/second. Sensor delivery rates are requested rates; actual delivered timestamps/counts are retained.

## Files and upload

Captures are visible in Files under **On My iPhone > DeepSplat**:

```text
captures/<UUID>/capture.json
captures/<UUID>/frames/000001.jpg ...
captures/<UUID>/photo.jpg
captures/<UUID>/imu/{accel,gyro,motion}.csv
captures/<UUID>/log.txt
archives/<UUID>.zip
mock-uploads/<UUID>.zip
```

The ZIP is produced with `NSFileCoordinator` `.forUploading`. Its contents may be wrapped in the capture UUID directory. The backend should locate `capture.json` and resolve its relative paths from that directory. Archive integrity and repeat export are covered by core tests. Local source captures are kept after upload for inspection/export; delete them explicitly when no longer needed.

Mock upload copies real ZIP bytes into `mock-uploads`; it does not simulate camera or sensor data. Disable Mock backend and enter the backend HTTPS URL and token to send real captures. Tokens are stored only in the Keychain, scoped by backend URL, and are not sent to the storage URL. No Google Cloud credentials are used.

Uploads run oldest-first, one at a time. Queue state is atomically persisted outside Documents. File-based PUT tasks use a background URLSession with launch-event restoration. Failed transfers request a fresh signed URL with the same capture ID and retry with exponential backoff capped at five minutes. Relaunch resumes pending jobs. A successful PUT is persisted before `/complete`; a failed completion request retries confirmation without uploading again. The Wi-Fi-only preference is captured when the job is queued. A system-suspended app can continue an already-scheduled transfer; retries/preparation need app execution time, and user force-quit prevents background relaunch until reopened.

Pull to refresh the captures list to request optional backend statuses. See [BACKEND.md](BACKEND.md) for the proposed API and idempotency requirements.

## Validation

```sh
cd Packages/DeepSplatCore
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer swift test
```

Core tests cover row-major transforms, intrinsics, tracking labels, 30/60 FPS sampling, IMU units/signs, buffered CSV flush, manifest encoding, file integrity, actual ZIP creation, stable upload IDs, signed requests, persistence and retry backoff. The Xcode test target adds mocked HTTP confirmation/authentication and idempotent mock-upload tests. Run those on an iPhone/Simulator through Xcode's Test action.

Inspect an exported real capture on your Mac without installing Python dependencies:

```sh
python3 Scripts/validate_capture.py /path/to/capture.zip
```

The script checks manifest files, raw JPEG dimensions/EXIF orientation, matrix values, monotonic frame/IMU timestamps, sample counts, and reports sampling gaps and sensor rates. It does not prove metric pose accuracy or backend localization.

### Milestone status

- **M1:** implemented; 21 core tests pass locally.
- **M2:** generated project and unsigned iPhone build pass. Installation/portrait behavior still require local signing and a connected iPhone.
- **M3:** implemented; real 20-second camera/IMU capture, printed-text orientation, face-up acceleration, GPS and tape-measure pose accuracy need device checks from the plan.
- **M4:** background queue/mock implementation and test target compile. Real device background/flight-mode recovery and signed GCS integration await device/backend configuration.
- **M5:** schema, backend contract and inspection script supplied; backend-team validation awaits a real exported capture.

### Clarifications and deviations

- If the time limit arrives without a crack photo, the app takes it automatically, logs that action, then stops. This resolves the plan's combination of an automatic time limit and requiring a crack photo before Stop. An in-flight high-resolution photo can add its capture latency to the nominal duration.
- Backgrounding or interruption stops a recording to preserve one consistent ARKit coordinate frame. Complete captures finalize; incomplete captures remain in Files with logs and are excluded from the upload list. A new capture starts a new AR session.
- GPS records both ellipsoidal and mean-sea-level altitude when vertical accuracy is valid. Both are null when it is invalid; MSL is never substituted for ellipsoidal altitude. GPS times out after five seconds, including the permission prompt.
- Static backend tokens are implemented. Automatic 401 token refresh is deferred because the plan specifies no refresh endpoint/provider. A 401 asks the user to update the token; automatic retry can then use the updated Keychain entry.
- No iPhone or Simulator runtime was available during implementation. Device recording, UI appearance, GPS, AirDrop, lock-screen background transfer and backend acceptance have not been claimed as verified.


## Native design

The capture list, review and settings screens use the warm paper, navy ink, blue actions,
flat bordered cards and pill controls from [DESIGN.md](../../DESIGN.md) and
[design/system/tokens.json](../../design/system/tokens.json). The camera uses the dark
viewport palette and pick reticle; its controls sit on a paper card. Layouts adapt the
[product reference](../../design/ux-layouts/imoscan_product_visuals.pdf) to the existing
sweep → crack photo → review workflow. Detection, linked historical findings and 3D
localization are not implied by the camera UI.

`UI/DesignSystem.swift` contains the native tokens and shared components. Typography
uses the system-ui / monospace fallbacks allowed by the token file, with Dynamic Type.
The wordmark and icon in the asset catalog are copies of the canonical SVGs in
`design/brand/logos`. The app icon is rasterized directly from the same SVG on an
opaque navy background. Regenerate it from this folder with:

```sh
swift Scripts/generate_app_icon.swift
```

This optional asset-generation step requires `rsvg-convert` from librsvg; building or
running the app needs no extra dependency. The redesign was checked with an unsigned
Debug build for a generic iOS device. There is no installed Simulator runtime on this
Mac, so on-device appearance, camera framing and larger accessibility sizes still
need a visual pass in Xcode on an iPhone.
