package com.deepsplat.capture.capture

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.PackageManager
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.os.Looper
import android.os.SystemClock
import androidx.core.content.ContextCompat
import com.deepsplat.capture.core.CaptureException
import com.deepsplat.capture.core.ClockMapper
import com.deepsplat.capture.core.CsvWriter
import com.deepsplat.capture.core.GeoHint
import com.deepsplat.capture.core.ImuCsv
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withTimeoutOrNull
import java.io.File
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resume

val systemClocks: () -> Pair<Long, Long> = { SystemClock.elapsedRealtimeNanos() to System.nanoTime() }

/**
 * Accelerometer, gyroscope and fused motion to the imu CSV files, in the iPhone app's columns and signs
 * (see CaptureManifest.CONVENTIONS). All callbacks and writes run on one handler thread.
 */
class ImuRecorder(context: Context) {
    private val sm = context.getSystemService(SensorManager::class.java)
    private var thread: HandlerThread? = null
    private var handler: Handler? = null
    private var accel: CsvWriter? = null
    private var gyro: CsvWriter? = null
    private var motion: CsvWriter? = null
    private val clock = ClockMapper(systemClocks)
    private var startT = 0.0
    private val gravity = FloatArray(3)
    private val linear = FloatArray(3)
    private val rate = FloatArray(3)
    private val q = FloatArray(4)

    val hasMotionSensors: Boolean
        get() = sm.getDefaultSensor(Sensor.TYPE_ACCELEROMETER) != null && sm.getDefaultSensor(Sensor.TYPE_GYROSCOPE) != null

    private val listener = object : SensorEventListener {
        override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) = Unit
        override fun onSensorChanged(e: SensorEvent) {
            val t = clock.seconds(e.timestamp)
            if (t < startT) return
            val v = e.values
            when (e.sensor.type) {
                Sensor.TYPE_ACCELEROMETER -> accel?.append(doubleArrayOf(t, v[0].toDouble(), v[1].toDouble(), v[2].toDouble()))
                Sensor.TYPE_GYROSCOPE -> {
                    rate[0] = v[0]; rate[1] = v[1]; rate[2] = v[2]
                    gyro?.append(doubleArrayOf(t, v[0].toDouble(), v[1].toDouble(), v[2].toDouble()))
                }
                Sensor.TYPE_GRAVITY -> { gravity[0] = v[0]; gravity[1] = v[1]; gravity[2] = v[2] }
                Sensor.TYPE_LINEAR_ACCELERATION -> { linear[0] = v[0]; linear[1] = v[1]; linear[2] = v[2] }
                Sensor.TYPE_GAME_ROTATION_VECTOR -> {
                    SensorManager.getQuaternionFromVector(q, v) // w, x, y, z
                    motion?.append(doubleArrayOf(
                        t, q[1].toDouble(), q[2].toDouble(), q[3].toDouble(), q[0].toDouble(),
                        -gravity[0].toDouble(), -gravity[1].toDouble(), -gravity[2].toDouble(),
                        -linear[0].toDouble(), -linear[1].toDouble(), -linear[2].toDouble(),
                        rate[0].toDouble(), rate[1].toDouble(), rate[2].toDouble(),
                    ))
                }
            }
        }
    }

    fun start(folder: File, rateHz: Double, startTimeS: Double) {
        if (!hasMotionSensors) throw CaptureException("This phone has no gyroscope, which the capture needs.")
        startT = startTimeS
        accel = CsvWriter(File(folder, "imu/accel.csv"), ImuCsv.ACCEL_HEADER)
        gyro = CsvWriter(File(folder, "imu/gyro.csv"), ImuCsv.GYRO_HEADER)
        motion = CsvWriter(File(folder, "imu/motion.csv"), ImuCsv.MOTION_HEADER)
        val t = HandlerThread("deepsplat-imu", android.os.Process.THREAD_PRIORITY_URGENT_DISPLAY).apply { start() }
        thread = t
        handler = Handler(t.looper)
        val period = (1_000_000 / rateHz).toInt()
        for (type in listOf(Sensor.TYPE_GRAVITY, Sensor.TYPE_LINEAR_ACCELERATION, Sensor.TYPE_GYROSCOPE,
            Sensor.TYPE_ACCELEROMETER, Sensor.TYPE_GAME_ROTATION_VECTOR)) {
            sm.getDefaultSensor(type)?.let { sm.registerListener(listener, it, period, handler) }
        }
    }

    /** Stops the sensors and closes the files; returns the sample counts. */
    fun stop(): Map<String, Int> {
        sm.unregisterListener(listener)
        val h = handler ?: return emptyMap()
        val done = CountDownLatch(1)
        var counts = emptyMap<String, Int>()
        h.post {
            counts = mapOf("accel" to (accel?.count ?: 0), "gyro" to (gyro?.count ?: 0), "motion" to (motion?.count ?: 0))
            listOf(accel, gyro, motion).forEach { runCatching { it?.close() } }
            accel = null; gyro = null; motion = null
            done.countDown()
        }
        done.await(3, TimeUnit.SECONDS)
        thread?.quitSafely()
        thread = null; handler = null
        return counts
    }

    val clockShiftNs: Long? get() = clock.resolvedShiftNs
}

/** One GPS fix as a coarse hint for the backend, or null when off, denied or not found in time. */
@SuppressLint("MissingPermission")
suspend fun locationHint(context: Context, timeoutMs: Long = 15_000): GeoHint? {
    if (ContextCompat.checkSelfPermission(context, Manifest.permission.ACCESS_FINE_LOCATION) != PackageManager.PERMISSION_GRANTED) return null
    val lm = context.getSystemService(LocationManager::class.java) ?: return null
    if (!lm.isProviderEnabled(LocationManager.GPS_PROVIDER)) return null
    val loc = withTimeoutOrNull(timeoutMs) {
        suspendCancellableCoroutine<Location?> { cont ->
            val l = object : LocationListener {
                override fun onLocationChanged(location: Location) {
                    lm.removeUpdates(this)
                    if (cont.isActive) cont.resume(location)
                }
                @Deprecated("Deprecated in Java")
                override fun onStatusChanged(provider: String?, status: Int, extras: android.os.Bundle?) = Unit
                override fun onProviderDisabled(provider: String) = Unit
                override fun onProviderEnabled(provider: String) = Unit
            }
            lm.requestLocationUpdates(LocationManager.GPS_PROVIDER, 0L, 0f, l, Looper.getMainLooper())
            cont.invokeOnCancellation { lm.removeUpdates(l) }
        }
    } ?: return null
    val vAcc = if (loc.hasVerticalAccuracy()) loc.verticalAccuracyMeters.toDouble() else -1.0
    val msl = if (Build.VERSION.SDK_INT >= 34 && loc.hasMslAltitude()) loc.mslAltitudeMeters else null
    return GeoHint(
        lat = loc.latitude, lon = loc.longitude,
        altitudeEllipsoidalM = if (loc.hasAltitude() && vAcc >= 0) loc.altitude else null,
        altitudeMslM = if (vAcc >= 0) msl else null,
        horizontalAccuracyM = if (loc.hasAccuracy()) loc.accuracy.toDouble() else -1.0,
        verticalAccuracyM = vAcc,
        tUnix = loc.time / 1000.0,
    )
}
