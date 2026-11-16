# deepsplat capture for Android

Native Android counterpart of the iPhone recorder in [`clients/ios`](../ios/README.md). It records a short
ARCore-tracked sweep plus a sharp defect photo, writes the **same `capture.json` schema v1 and zip layout**,
and uploads through the same `/v1/captures` contract ([`clients/ios/BACKEND.md`](../ios/BACKEND.md)).
The backend handles Android and iPhone captures with one code path; nothing changes on the server.

Kotlin, Jetpack Compose, ARCore, WorkManager, OkHttp. The UI uses the web UI's design tokens one to one
([`DESIGN.md`](../../DESIGN.md), [`design/system/tokens.css`](../../design/system/tokens.css)): warm paper UI,
navy ink, one blue action per view, rust only for open findings, a dark viewport with the pick reticle, and
Space Grotesk / IBM Plex Sans / IBM Plex Mono.

## Build and install

Requirements: JDK 17 and the Android SDK (platform 36). Point `local.properties` at the SDK:

```bash
cd clients/android
echo "sdk.dir=$HOME/Android/Sdk" > local.properties
./gradlew assembleDebug                 # app/build/outputs/apk/debug/app-debug.apk
adb install -r app/build/outputs/apk/debug/app-debug.apk
./gradlew testDebugUnitTest lintDebug   # contract, geometry, zip and HTTP tests
./gradlew testDebugUnitTest -Pscreens   # UI screenshots without a phone: app/build/screens
```

The phone needs ARCore support ("Google Play Services for AR"; the app asks to install it on first start).

### Release

Create `clients/android/keystore.properties` (not in git):

```properties
storeFile=release.jks
storePassword=…
keyAlias=deepsplat
keyPassword=…
```

then `./gradlew bundleRelease` for the Play Store bundle (`app/build/outputs/bundle/release/`). R8 shrinks the
release APK to about 3 MB. Bump `versionCode` / `versionName` in `app/build.gradle.kts` for every upload.

## Connect to the backend

1. Start the backend on the laptop: `scripts/start-backend.sh`. It prints the network address, for example
   `http://192.168.1.20:8000`, and the token is `API_TOKEN` in `.env`.
2. In the app: **Settings → Backend**, enter the address (the scheme is optional; `192.168.1.20:8000` works),
   enter the token, then tap **Test connection**. It checks that the backend answers, that the token is
   accepted and whether damage detection is online.
3. Choose the **site**. The list comes from `GET /maps`. "Placement ready" means the map has a localization
   index (views.zip); without one, captures upload but cannot be placed.

Plain `http://` is only accepted for private-network hosts (10.x, 172.16–31.x, 192.168.x, 169.254.x,
127.x, localhost, *.local), the same rule as the iPhone app. Everything else needs `https://`.

## Recording flow

1. **Sweep**: start a few metres away and walk slowly towards the defect. Tracked feature points are drawn
   on the walls so people see when the phone "has" the room. Frames are kept at 5 fps (configurable).
2. **Defect photo**: tap the shutter. The app takes a burst of 8 frames and keeps the sharpest one
   (Laplacian variance), which avoids the blurry single frame people get when tapping.
3. **Review**: the capture is saved and, by default, uploaded right away in the background (WorkManager,
   survives app restarts and retries with the iPhone's 2 s → 5 min back-off). The capture screen then
   follows backend processing: *Processing*, *Placed in scan*, *Not placed* (no match in the scan),
   *Waiting for detection* and the findings with their size once the backend reports them, plus the
   backend's detection overlay.

At 20 s (configurable) the app takes the photo automatically if there is none yet and stops.

## Test imports

The import button on the home screen (next to Settings) adds captures that were not recorded on this phone.
They upload to the site chosen in Settings like any other capture.

- **Capture zip**: a zip in the iPhone app's format, with or without the `<capture_id>/` folder. The files stay
  byte for byte as recorded (frames, poses, IMU, photo), so the backend gets exactly what the iPhone sent.
- **Video** from the gallery: frames at the recording frame rate, the sharpest frame becomes the photo. A plain
  video has no tracking, so frames carry no pose (`camera_to_world: null`, `source: "video_import"`) and the
  backend localizes the photo on its own (`vps_photo`). Intrinsics are estimated (fx = 0.76 × long side).

## Data contract (same as iOS)

| | iPhone (ARKit) | Android (ARCore) |
|---|---|---|
| Images | raw sensor orientation, turn 90° clockwise for portrait | same. Phones with `SENSOR_ORIENTATION` 270 are turned 180° (image, intrinsics and pose) so the rule holds |
| `camera_to_world` | `ARCamera.transform`, row-major, OpenGL camera axes | `Camera.getPose()` of the physical camera, row-major, OpenGL axes relative to the image readout |
| `intrinsics` | for the stored image | `Camera.getImageIntrinsics()`, scaled to the stored CPU image |
| World | gravity-aligned, +Y up, metric | same |
| `t` | seconds of uptime | seconds of `CLOCK_BOOTTIME`; camera and sensor clocks are mapped onto it |
| `uptime_to_unix_offset` | unix − uptime | unix − boottime |
| IMU | CoreMotion | `TYPE_ACCELEROMETER` (specific force), `TYPE_GYROSCOPE`, `TYPE_GAME_ROTATION_VECTOR`, gravity and user acceleration with CoreMotion signs |
| Photo | high-resolution ARKit frame or fallback | sharpest of an 8-frame burst of the CPU image, `high_res: false` |
| Zip | `<capture_id>/…` | `<capture_id>/…`, entries stored (JPEGs don't compress) |

Android adds `platform: "android"` and `os_version`; `conventions` documents the Android sources. The CPU image
is the largest ARCore offers up to 1920 px (usually 1920 × 1080).

Verified against the backend: the unit test `SampleExport` writes `app/build/sample-capture/capture.zip`, which
`backend/package.py` reads and turns upright, `clients/ios/Scripts/validate_capture.py` reports PASS for, and which
runs through `POST /v1/captures` → `PUT` → `/complete` → `GET /v1/captures/{id}` on a local backend.

## Code map

| Path | What |
|---|---|
| `core/` | Platform-free logic with unit tests: manifest schema, pose and intrinsics maths, YUV → NV21, sharpness, frame sampler, clock mapping, capture folder and zip, URL rules |
| `capture/CaptureEngine.kt` | ARCore session, camera config, GL rendering, frame sampling, photo burst, finishing the capture |
| `capture/Renderers.kt` | Camera background and tracked-point renderers (GLES 2) |
| `capture/Sensors.kt` | IMU recorder and the optional GPS hint |
| `data/` | Capture list, settings and Keystore-encrypted token, backend client, upload queue and worker |
| `ui/` | Theme from the design tokens, components (pill buttons, cards, chips, reticle, wordmark) and the screens |
