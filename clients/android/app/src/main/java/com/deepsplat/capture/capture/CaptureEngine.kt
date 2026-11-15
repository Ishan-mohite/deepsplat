package com.deepsplat.capture.capture

import android.app.Activity
import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.ImageFormat
import android.graphics.Matrix
import android.graphics.Rect
import android.graphics.YuvImage
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraManager
import android.hardware.display.DisplayManager
import android.opengl.GLES20
import android.opengl.GLSurfaceView
import android.opengl.Matrix as GlMatrix
import android.os.Build
import android.os.SystemClock
import android.view.Surface
import android.view.Display
import com.deepsplat.capture.AppGraph
import com.deepsplat.capture.BuildConfig
import com.deepsplat.capture.core.CaptureException
import com.deepsplat.capture.core.CaptureManifest
import com.deepsplat.capture.core.CaptureWriter
import com.deepsplat.capture.core.ClockMapper
import com.deepsplat.capture.core.FrameRecord
import com.deepsplat.capture.core.FrameSampler
import com.deepsplat.capture.core.GeoHint
import com.deepsplat.capture.core.Imu
import com.deepsplat.capture.core.Intrinsics
import com.deepsplat.capture.core.Pose
import com.deepsplat.capture.core.Video
import com.deepsplat.capture.core.lumaSharpness
import com.deepsplat.capture.core.needsHalfTurn
import com.deepsplat.capture.core.rotateNv21By180
import com.deepsplat.capture.core.rotated180
import com.deepsplat.capture.core.yuv420ToNv21
import com.deepsplat.capture.data.CaptureConfiguration
import com.deepsplat.capture.data.CaptureRecord
import com.google.ar.core.ArCoreApk
import com.google.ar.core.Camera
import com.google.ar.core.CameraConfig
import com.google.ar.core.CameraConfigFilter
import com.google.ar.core.Config
import com.google.ar.core.Frame
import com.google.ar.core.Session
import com.google.ar.core.TrackingFailureReason
import com.google.ar.core.TrackingState
import com.google.ar.core.exceptions.CameraNotAvailableException
import com.google.ar.core.exceptions.NotYetAvailableException
import com.google.ar.core.exceptions.UnavailableException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.io.ByteArrayOutputStream
import java.util.EnumSet
import java.util.Locale
import java.util.UUID
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean
import javax.microedition.khronos.egl.EGLConfig
import javax.microedition.khronos.opengles.GL10

enum class Phase { STARTING, READY, RECORDING, SAVING, SAVED, FAILED }

data class CaptureUi(
    val phase: Phase = Phase.STARTING,
    /** "normal", "limited:<reason>" or "not_available", as written into capture.json. */
    val tracking: String = "not_available",
    val trackingWarning: Boolean = false,
    val elapsed: Double = 0.0,
    val frames: Int = 0,
    val hasPhoto: Boolean = false,
    val takingPhoto: Boolean = false,
    val photoPreview: Bitmap? = null,
    val points: Int = 0,
    val imageSize: String = "",
    val savedId: String? = null,
    val message: String? = null,
    val fatal: String? = null,
)

/**
 * ARCore session, camera preview and the recording itself. ARCore runs on the GL thread; JPEG encoding
 * and every file write run on one IO thread that owns the [CaptureWriter]. The UI talks to the engine
 * through [ui] and the request methods, which are safe from any thread.
 */
class CaptureEngine(private val context: Context, private val graph: AppGraph, private val config: CaptureConfiguration) :
    GLSurfaceView.Renderer {

    private val _ui = MutableStateFlow(CaptureUi())
    val ui: StateFlow<CaptureUi> = _ui.asStateFlow()

    private var session: Session? = null
    private var installRequested = false
    private val background = BackgroundRenderer()
    private val points = PointCloudRenderer()
    private var viewportChanged = false
    private var viewW = 0
    private var viewH = 0
    private var cameraConfig: CameraConfig? = null
    private var halfTurn = false
    private var sensorOrientation = 90
    private val density = context.resources.displayMetrics.density
    @Volatile var showPoints = true

    // Recording state. Flags are set from the UI thread and read on the GL thread.
    private val recording = AtomicBoolean(false)
    private val startRequested = AtomicBoolean(false)
    private val stopRequested = AtomicBoolean(false)
    private val photoRequested = AtomicBoolean(false)
    @Volatile private var photoBusy = false
    @Volatile private var hasPhoto = false
    private var autoPhoto = false
    private val io = Executors.newSingleThreadExecutor { Thread(it, "deepsplat-capture-io").apply { priority = Thread.NORM_PRIORITY + 1 } }
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private var writer: CaptureWriter? = null
    private var imu: ImuRecorder? = null
    private val clock = ClockMapper(systemClocks)
    private var sampler = FrameSampler(config.fps, 30.0)
    private var startT = 0.0
    private var lastT = 0.0
    private var createdUnix = 0.0
    private var offset = 0.0
    private var trackingLostAt: Double? = null
    @Volatile private var geo: GeoHint? = null
    private var pool: ArrayBlockingQueue<ByteArray>? = null
    private var frameBytes = 0
    private val burst = 8
    private var burstLeft = 0
    private var burstBest: Capture? = null
    private var burstSpare: ByteArray? = null
    private var burstBestScore = -1.0
    private var lastUiPush = 0L
    private var pointCount = 0

    /** Runs on the IO thread; a failing task must never take the app down. */
    private fun onIo(block: () -> Unit) {
        if (io.isShutdown) return
        io.execute { try { block() } catch (e: Throwable) { android.util.Log.w("deepsplat", "capture io", e) } }
    }

    private class Capture(val nv21: ByteArray, val width: Int, val height: Int, val record: FrameRecord)

    // ------------------------------------------------------------------ lifecycle (UI thread)

    /** Call from onResume. Returns false while ARCore is being installed or is unavailable. */
    fun resume(activity: Activity): Boolean {
        if (session == null) {
            try {
                when (ArCoreApk.getInstance().requestInstall(activity, !installRequested)) {
                    ArCoreApk.InstallStatus.INSTALL_REQUESTED -> { installRequested = true; return false }
                    else -> Unit
                }
                session = Session(activity).also { configure(it) }
            } catch (e: UnavailableException) {
                fatal(arcoreMessage(e)); return false
            } catch (e: Exception) {
                fatal("The camera could not be started: ${e.message}"); return false
            }
        }
        return try {
            session?.resume(); true
        } catch (e: CameraNotAvailableException) {
            fatal("The camera is in use by another app. Close it and try again."); false
        }
    }

    fun pause() {
        if (recording.get()) interrupt()
        session?.pause()
    }

    fun close() {
        if (recording.get()) interrupt()
        session?.close()
        session = null
        io.shutdown()
        scope.cancel()
    }

    private fun configure(s: Session) {
        val filter = CameraConfigFilter(s)
            .setFacingDirection(CameraConfig.FacingDirection.BACK)
            .setTargetFps(EnumSet.of(CameraConfig.TargetFps.TARGET_FPS_30))
            .setDepthSensorUsage(EnumSet.of(CameraConfig.DepthSensorUsage.DO_NOT_USE))
        val configs = s.getSupportedCameraConfigs(filter).ifEmpty { s.getSupportedCameraConfigs(CameraConfigFilter(s)) }
        // The largest CPU image up to 1920 px (the iPhone records 1920 x 1440): these are the frames the
        // backend sees. Bigger sensors would cost ~18 MB per buffered frame for no localization gain.
        val fits = configs.filter { maxOf(it.imageSize.width, it.imageSize.height) <= 1920 }
        val best = fits.maxByOrNull { it.imageSize.width * it.imageSize.height }
            ?: configs.minByOrNull { it.imageSize.width * it.imageSize.height }
        if (best != null) s.cameraConfig = best
        cameraConfig = s.cameraConfig
        sensorOrientation = runCatching {
            context.getSystemService(CameraManager::class.java).getCameraCharacteristics(s.cameraConfig.cameraId)
                .get(CameraCharacteristics.SENSOR_ORIENTATION) ?: 90
        }.getOrDefault(90)
        halfTurn = needsHalfTurn(sensorOrientation)
        val size = s.cameraConfig.imageSize
        frameBytes = size.width * size.height * 3 / 2
        _ui.update { it.copy(imageSize = "${size.width} × ${size.height}") }
        s.configure(Config(s).apply {
            focusMode = Config.FocusMode.AUTO
            updateMode = Config.UpdateMode.BLOCKING
            planeFindingMode = Config.PlaneFindingMode.DISABLED
            lightEstimationMode = Config.LightEstimationMode.DISABLED
            depthMode = Config.DepthMode.DISABLED
            instantPlacementMode = Config.InstantPlacementMode.DISABLED
        })
    }

    private fun arcoreMessage(e: UnavailableException): String = when (e) {
        is com.google.ar.core.exceptions.UnavailableArcoreNotInstalledException,
        is com.google.ar.core.exceptions.UnavailableUserDeclinedInstallationException ->
            "Install Google Play Services for AR to record captures."
        is com.google.ar.core.exceptions.UnavailableApkTooOldException -> "Update Google Play Services for AR to record captures."
        is com.google.ar.core.exceptions.UnavailableSdkTooOldException -> "Update this app to record captures."
        is com.google.ar.core.exceptions.UnavailableDeviceNotCompatibleException -> "This phone does not support AR tracking."
        else -> "AR tracking is not available on this phone."
    }

    private fun fatal(message: String) = _ui.update { it.copy(phase = Phase.FAILED, fatal = message) }

    fun consumeMessage() = _ui.update { it.copy(message = null) }

    // ------------------------------------------------------------------ requests (any thread)

    fun start() {
        val u = _ui.value
        if (u.phase != Phase.READY || u.tracking != "normal") return
        if (config.mapId.isBlank()) { _ui.update { it.copy(message = "Choose a site in Settings before recording.") }; return }
        startRequested.set(true)
    }

    fun takePhoto() {
        if (recording.get() && !photoBusy) photoRequested.set(true)
    }

    fun stop() {
        if (recording.get() && hasPhoto && !photoBusy) stopRequested.set(true)
    }

    fun discard() {
        val wasRecording = recording.getAndSet(false)
        val w = writer
        writer = null
        onIo {
            if (wasRecording) runCatching { imu?.stop() }
            w?.folder?.deleteRecursively()
        }
        _ui.update { CaptureUi(phase = Phase.READY, tracking = it.tracking, imageSize = it.imageSize) }
    }

    /** The app left the screen while recording: save if a photo exists, otherwise drop the capture. */
    private fun interrupt() {
        if (hasPhoto && !photoBusy) {
            recording.set(false)
            finish("Capture stopped when the app moved to the background. Frames up to that moment are saved.")
        } else {
            discard()
            _ui.update { it.copy(message = "Capture stopped when the app moved to the background. It had no defect photo, so it was not kept.") }
        }
    }

    // ------------------------------------------------------------------ GL thread

    override fun onSurfaceCreated(gl: GL10?, config: EGLConfig?) {
        GLES20.glClearColor(0x1A / 255f, 0x21 / 255f, 0x30 / 255f, 1f)
        background.create()
        points.create()
    }

    override fun onSurfaceChanged(gl: GL10?, width: Int, height: Int) {
        GLES20.glViewport(0, 0, width, height)
        viewW = width; viewH = height; viewportChanged = true
    }

    /** The engine holds the application context, which has no display of its own: ask the DisplayManager. */
    private fun displayRotation(): Int =
        context.getSystemService(DisplayManager::class.java)?.getDisplay(Display.DEFAULT_DISPLAY)?.rotation ?: Surface.ROTATION_0

    override fun onDrawFrame(gl: GL10?) {
        GLES20.glClear(GLES20.GL_COLOR_BUFFER_BIT or GLES20.GL_DEPTH_BUFFER_BIT)
        val s = session ?: return
        if (viewportChanged) { s.setDisplayGeometry(displayRotation(), viewW, viewH); viewportChanged = false }
        s.setCameraTextureName(background.textureId)
        val frame = try { s.update() } catch (e: CameraNotAvailableException) {
            if (recording.get()) interrupt()
            return
        } catch (e: Exception) { return }
        val camera = frame.camera
        background.draw(frame)

        val tracking = trackingValue(camera)
        val now = SystemClock.elapsedRealtimeNanos() / 1e9
        if (tracking == "normal") trackingLostAt = null else if (trackingLostAt == null) trackingLostAt = now

        if (showPoints && camera.trackingState == TrackingState.TRACKING) {
            val proj = FloatArray(16); val view = FloatArray(16); val mvp = FloatArray(16)
            camera.getProjectionMatrix(proj, 0, 0.05f, 50f)
            camera.getViewMatrix(view, 0)
            GlMatrix.multiplyMM(mvp, 0, proj, 0, view, 0)
            frame.acquirePointCloud().use { pointCount = points.draw(it, mvp, density) }
        } else pointCount = 0

        if (startRequested.getAndSet(false) && tracking == "normal") beginRecording(frame)

        if (recording.get()) record(frame, camera, tracking)

        val nowMs = SystemClock.uptimeMillis()
        if (nowMs - lastUiPush > 100) {
            lastUiPush = nowMs
            val lostFor = trackingLostAt?.let { now - it } ?: 0.0
            _ui.update {
                it.copy(
                    phase = if (it.phase == Phase.STARTING && camera.trackingState != TrackingState.STOPPED) Phase.READY else it.phase,
                    tracking = tracking, trackingWarning = lostFor > 2.0, points = pointCount,
                    elapsed = if (recording.get()) lastT - startT else it.elapsed,
                )
            }
        }
    }

    private fun trackingValue(camera: Camera): String = when (camera.trackingState) {
        TrackingState.TRACKING -> "normal"
        TrackingState.PAUSED -> "limited:" + when (camera.trackingFailureReason) {
            TrackingFailureReason.NONE -> "initializing"
            TrackingFailureReason.EXCESSIVE_MOTION -> "excessive_motion"
            TrackingFailureReason.INSUFFICIENT_FEATURES -> "insufficient_features"
            TrackingFailureReason.INSUFFICIENT_LIGHT -> "insufficient_light"
            TrackingFailureReason.CAMERA_UNAVAILABLE -> "camera_unavailable"
            else -> "unknown"
        }
        else -> "not_available"
    }

    private fun beginRecording(frame: Frame) {
        val id = UUID.randomUUID().toString().uppercase(Locale.ROOT)
        val bootNow = SystemClock.elapsedRealtimeNanos() / 1e9
        startT = clock.seconds(frame.timestamp)
        lastT = startT
        createdUnix = System.currentTimeMillis() / 1000.0
        offset = createdUnix - bootNow
        sampler = FrameSampler(config.fps, 30.0)
        hasPhoto = false; photoBusy = false; autoPhoto = false; burstLeft = 0; burstBest = null
        pool = ArrayBlockingQueue<ByteArray>(4).apply { repeat(4) { offer(ByteArray(frameBytes)) } }
        burstSpare = ByteArray(frameBytes)
        geo = null
        val w = try {
            CaptureWriter(graph.store.capturesDir, id) { SystemClock.elapsedRealtimeNanos() / 1e9 }
        } catch (e: Exception) {
            _ui.update { it.copy(message = e.message) }; return
        }
        writer = w
        val rec = ImuRecorder(context)
        imu = rec
        try {
            rec.start(w.folder, config.imuRateHz, startT)
        } catch (e: Exception) {
            w.folder.deleteRecursively(); writer = null
            _ui.update { it.copy(message = e.message) }; return
        }
        recording.set(true)
        val cc = cameraConfig
        onIo {
            w.log("Recording started on ${Build.MANUFACTURER} ${Build.MODEL}, Android ${Build.VERSION.RELEASE}, app ${BuildConfig.VERSION_NAME}.")
            w.log("Camera ${cc?.cameraId} CPU image ${cc?.imageSize}, texture ${cc?.textureSize}, sensor orientation $sensorOrientation, half turn applied: $halfTurn.")
            w.log("Time base: CLOCK_BOOTTIME seconds; camera clock shift ${clock.resolvedShiftNs} ns.")
        }
        if (config.recordGps) scope.launch { geo = locationHint(context) }
        _ui.update { it.copy(phase = Phase.RECORDING, elapsed = 0.0, frames = 0, hasPhoto = false, photoPreview = null, savedId = null) }
    }

    /** Copies the CPU image of [frame] with its pose, or null if the image is not ready yet. */
    private fun grab(frame: Frame, camera: Camera, into: ByteArray, tracking: String, t: Double, highRes: Boolean?): Capture? {
        val image = try { frame.acquireCameraImage() } catch (e: NotYetAvailableException) { return null } catch (e: Exception) { return null }
        image.use { img ->
            if (img.format != ImageFormat.YUV_420_888) return null
            val w = img.width; val h = img.height
            if (into.size < w * h * 3 / 2) return null
            val p = img.planes
            yuv420ToNv21(w, h, p[0].buffer, p[0].rowStride, p[0].pixelStride, p[1].buffer, p[2].buffer, p[1].rowStride, p[1].pixelStride, into)
            val k = camera.imageIntrinsics
            val dims = k.imageDimensions
            val sx = w.toFloat() / dims[0]; val sy = h.toFloat() / dims[1]
            var intrinsics = Intrinsics(k.focalLength[0] * sx, k.focalLength[1] * sy, k.principalPoint[0] * sx, k.principalPoint[1] * sy, w, h)
            val m = FloatArray(16)
            camera.pose.toMatrix(m, 0)
            var pose = Pose.rowMajor(m)
            if (halfTurn) {
                rotateNv21By180(into, w, h)
                intrinsics = intrinsics.rotated180()
                pose = Pose.rotated180(pose)
            }
            return Capture(into, w, h, FrameRecord("", t, pose, intrinsics, "portrait", tracking, highRes))
        }
    }

    private fun record(frame: Frame, camera: Camera, tracking: String) {
        val w = writer ?: return
        val t = clock.seconds(frame.timestamp)
        if (t <= lastT && t != startT) return // same frame twice
        lastT = t

        // Video frames at the target rate.
        val p = pool
        if (p != null && sampler.shouldKeep(t)) {
            val buf = p.poll()
            if (buf == null) {
                onIo { w.log("Frame at %.3f skipped: encoder busy.".format(Locale.ROOT, t)) }
            } else {
                val c = grab(frame, camera, buf, tracking, t, null)
                if (c == null) p.offer(buf) else onIo {
                    try {
                        w.appendFrame(jpeg(c, config.jpegQuality), c.record)
                        val n = w.frames.size
                        _ui.update { it.copy(frames = n) }
                    } catch (e: Exception) {
                        w.log("Frame write failed: ${e.message}")
                    } finally { p.offer(buf) }
                }
            }
        }

        // Defect photo: the sharpest of a short burst.
        if (photoRequested.getAndSet(false) && !photoBusy) {
            photoBusy = true; burstLeft = burst; burstBest = null; burstBestScore = -1.0
            _ui.update { it.copy(takingPhoto = true) }
        }
        if (burstLeft > 0) {
            val spare = burstSpare ?: ByteArray(frameBytes)
            val c = grab(frame, camera, spare, tracking, t, false)
            if (c != null) {
                burstLeft--
                val score = lumaSharpness(c.nv21, c.width, c.height)
                if (score > burstBestScore) {
                    burstSpare = burstBest?.nv21 ?: ByteArray(frameBytes)
                    burstBest = c; burstBestScore = score
                } else burstSpare = spare
            }
            if (burstLeft == 0) {
                val best = burstBest
                burstBest = null
                if (best != null) savePhoto(w, best, burstBestScore) else { photoBusy = false; _ui.update { it.copy(takingPhoto = false) } }
            }
        }

        // Duration limit: take the photo automatically if missing, then stop.
        if (t - startT >= config.maxDuration) {
            if (hasPhoto && !photoBusy) stopRequested.set(true)
            else if (!photoBusy && !autoPhoto) {
                autoPhoto = true
                onIo { w.log("Maximum duration reached; taking the required defect photo automatically.") }
                photoRequested.set(true)
            }
        }
        if (stopRequested.getAndSet(false) && hasPhoto && !photoBusy) {
            recording.set(false)
            finish(null)
        }
    }

    private fun savePhoto(w: CaptureWriter, c: Capture, score: Double) {
        val copy = c.nv21.copyOf(c.width * c.height * 3 / 2)
        onIo {
            try {
                w.replacePhoto(jpeg(Capture(copy, c.width, c.height, c.record), 0.95), c.record)
                w.log("Defect photo saved: sharpest of $burst frames (score %.1f), ${c.width}x${c.height}.".format(Locale.ROOT, score))
                hasPhoto = true
                val preview = runCatching { previewOf(graph.store.photo(w.id).path) }.getOrNull()
                _ui.update { it.copy(hasPhoto = true, takingPhoto = false, photoPreview = preview) }
            } catch (e: Exception) {
                w.log("Defect photo failed: ${e.message}")
                _ui.update { it.copy(takingPhoto = false, message = "The photo could not be saved. Try again.") }
            } finally { photoBusy = false }
        }
    }

    private fun jpeg(c: Capture, quality: Double): ByteArray {
        val out = ByteArrayOutputStream(c.width * c.height / 3)
        if (!YuvImage(c.nv21, ImageFormat.NV21, c.width, c.height, null).compressToJpeg(Rect(0, 0, c.width, c.height), (quality * 100).toInt(), out)) {
            throw CaptureException("JPEG encoding failed.")
        }
        return out.toByteArray()
    }

    private fun finish(note: String?) {
        val w = writer ?: return
        val rec = imu
        val duration = lastT - startT
        _ui.update { it.copy(phase = Phase.SAVING) }
        onIo {
            try {
                val counts = rec?.stop().orEmpty()
                if ((counts["accel"] ?: 0) == 0 || (counts["gyro"] ?: 0) == 0) throw CaptureException("Motion data is missing, so the capture cannot be placed.")
                val photo = w.photo ?: throw CaptureException("No defect photo was saved.")
                rec?.clockShiftNs?.let { w.log("Motion sensor clock shift $it ns.") }
                val manifest = CaptureManifest(
                    captureId = w.id, mapId = config.mapId, createdUnix = createdUnix,
                    device = "${Build.MANUFACTURER} ${Build.MODEL}",
                    osVersion = "Android ${Build.VERSION.RELEASE} (API ${Build.VERSION.SDK_INT})",
                    appVersion = "${BuildConfig.VERSION_NAME} (${BuildConfig.VERSION_CODE})",
                    uptimeToUnixOffset = offset,
                    video = Video(config.fps, 30.0, duration, w.frames.toList()),
                    photo = photo,
                    imu = Imu(rateHz = config.imuRateHz, sampleCounts = counts),
                    geo = geo,
                )
                w.finish(manifest)
                val s = graph.settings.value
                graph.store.add(CaptureRecord(w.id, config.mapId, s.mapName.ifBlank { null }, createdUnix, w.frames.size, duration))
                val queueError = if (s.autoUpload) graph.uploads.enqueue(w.id) else null
                writer = null
                _ui.update { it.copy(phase = Phase.SAVED, savedId = w.id, message = note ?: queueError) }
            } catch (e: Exception) {
                w.log("Capture could not be finalized: ${e.message}")
                w.folder.deleteRecursively()
                writer = null
                _ui.update { CaptureUi(phase = Phase.READY, tracking = it.tracking, imageSize = it.imageSize, message = "The capture could not be saved: ${e.message}") }
            }
        }
    }

    companion object {
        /** The stored photo is in sensor orientation; people see it upright (turned 90 degrees clockwise). */
        fun previewOf(path: String, maxSide: Int = 720): Bitmap? {
            val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            BitmapFactory.decodeFile(path, bounds)
            if (bounds.outWidth <= 0) return null
            var sample = 1
            while (maxOf(bounds.outWidth, bounds.outHeight) / (sample * 2) >= maxSide) sample *= 2
            val bmp = BitmapFactory.decodeFile(path, BitmapFactory.Options().apply { inSampleSize = sample }) ?: return null
            return Bitmap.createBitmap(bmp, 0, 0, bmp.width, bmp.height, Matrix().apply { postRotate(90f) }, true)
        }
    }
}
