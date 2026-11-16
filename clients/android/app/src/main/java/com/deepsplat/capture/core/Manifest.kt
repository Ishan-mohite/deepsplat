package com.deepsplat.capture.core

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

/**
 * capture.json, schema version 1. Same contract as the iOS app (clients/ios, DeepSplatCore) so the
 * backend reads Android and iPhone captures with one code path (backend/package.py).
 */
@Serializable
data class Intrinsics(
    val fx: Float,
    val fy: Float,
    val cx: Float,
    val cy: Float,
    val width: Int,
    val height: Int,
)

@Serializable
data class FrameRecord(
    val file: String,
    /** Seconds on the capture's time base (see [CaptureManifest.timeBase]). */
    val t: Double,
    /** Camera to world, row-major 4x4, OpenGL camera axes (+X right, +Y up, looking along -Z). Null for imported videos. */
    @SerialName("camera_to_world") val cameraToWorld: List<Float>?,
    val intrinsics: Intrinsics,
    @SerialName("interface_orientation") val interfaceOrientation: String = "portrait",
    @SerialName("tracking_state") val trackingState: String,
    @SerialName("high_res") val highRes: Boolean? = null,
)

@Serializable
data class GeoHint(
    val lat: Double,
    val lon: Double,
    @SerialName("altitude_ellipsoidal_m") val altitudeEllipsoidalM: Double?,
    @SerialName("altitude_msl_m") val altitudeMslM: Double?,
    @SerialName("horizontal_accuracy_m") val horizontalAccuracyM: Double,
    @SerialName("vertical_accuracy_m") val verticalAccuracyM: Double,
    @SerialName("t_unix") val tUnix: Double,
)

@Serializable
data class Video(
    @SerialName("fps_target") val fpsTarget: Double,
    @SerialName("source_fps") val sourceFps: Double,
    @SerialName("duration_s") val durationS: Double,
    val frames: List<FrameRecord>,
)

@Serializable
data class Imu(
    @SerialName("rate_hz") val rateHz: Double,
    val files: Map<String, String> = mapOf("accel" to "imu/accel.csv", "gyro" to "imu/gyro.csv", "motion" to "imu/motion.csv"),
    @SerialName("motion_reference_frame") val motionReferenceFrame: String = "android_game_rotation_vector",
    @SerialName("sample_counts") val sampleCounts: Map<String, Int>,
)

@Serializable
data class CaptureManifest(
    @SerialName("schema_version") val schemaVersion: Int = 1,
    @SerialName("capture_id") val captureId: String,
    @SerialName("map_id") val mapId: String,
    @SerialName("created_unix") val createdUnix: Double,
    val device: String,
    val platform: String = "android",
    /** "arcore" for recorded captures, "video_import" for test videos from the gallery (no tracking, estimated intrinsics). */
    val source: String = "arcore",
    @SerialName("os_version") val osVersion: String = "",
    /** Set in captures from the iPhone app (imported zips). */
    @SerialName("ios_version") val iosVersion: String? = null,
    @SerialName("app_version") val appVersion: String,
    @SerialName("time_base") val timeBase: String = TIME_BASE,
    @SerialName("uptime_to_unix_offset") val uptimeToUnixOffset: Double,
    val conventions: Map<String, String> = CONVENTIONS,
    val video: Video,
    val photo: FrameRecord,
    val imu: Imu,
    /** Explicit null when GPS was off, denied or timed out. */
    val geo: GeoHint?,
) {
    fun validate() {
        fun fail(message: String): Nothing = throw CaptureException(message)
        if (schemaVersion != 1 || mapId.isBlank() || video.frames.isEmpty() || !video.durationS.isFinite() ||
            video.durationS < 0 || !createdUnix.isFinite() || !uptimeToUnixOffset.isFinite()
        ) fail("Capture is missing its map, frames or time base.")
        for (frame in video.frames + photo) {
            val pose = frame.cameraToWorld
            if (pose != null) Pose.requireValid(pose) else if (source == "arcore") fail("Recorded frames need a pose.")
            val k = frame.intrinsics
            if (!frame.t.isFinite() || k.width <= 0 || k.height <= 0 || k.fx <= 0f || k.fy <= 0f ||
                !k.cx.isFinite() || !k.cy.isFinite() || frame.interfaceOrientation != "portrait"
            ) fail("Invalid frame metadata.")
        }
        if (photo.file != "photo.jpg" || photo.highRes == null) fail("Capture requires a defect photo.")
    }

    /** Every file the manifest points to, relative to the capture folder. */
    val referencedFiles: List<String>
        get() = (video.frames + photo).map { it.file } + imu.files.values + "log.txt"

    companion object {
        const val TIME_BASE = "seconds of SystemClock.elapsedRealtime (CLOCK_BOOTTIME, includes deep sleep)"
        val CONVENTIONS = mapOf(
            "images" to "raw camera sensor orientation, rotated so that turning the image 90 degrees clockwise gives the upright portrait view (same as the iPhone app); no EXIF orientation",
            "camera_to_world" to "ARCore Camera.getPose() of the physical camera, row-major 4x4; world right-handed, metric, +Y up, origin at session start; camera +X right, +Y up along the stored image, looking along -Z",
            "intrinsics" to "pixels, for the stored image size (ARCore Camera.getImageIntrinsics)",
            "imu_axes" to "device frame: +X right, +Y top of phone, +Z out of screen",
            "accel" to "specific force in m/s^2 (Android TYPE_ACCELEROMETER); face up reads +9.81 on Z",
            "motion_gravity_and_user_acceleration" to "CoreMotion signs: gravity = -TYPE_GRAVITY, user acceleration = -TYPE_LINEAR_ACCELERATION, m/s^2",
            "motion_quaternion" to "TYPE_GAME_ROTATION_VECTOR, device to world, world Z vertical, heading arbitrary",
            "gyro" to "TYPE_GYROSCOPE, rad/s, counter-clockwise positive",
        )
    }
}

class CaptureException(message: String) : Exception(message)

val CaptureJson = Json {
    prettyPrint = true
    encodeDefaults = true
    explicitNulls = true
    ignoreUnknownKeys = true
}
