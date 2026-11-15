package com.deepsplat.capture

import com.deepsplat.capture.core.FrameSampler
import com.deepsplat.capture.core.Intrinsics
import com.deepsplat.capture.core.Pose
import com.deepsplat.capture.core.clockShiftToBoottime
import com.deepsplat.capture.core.isPrivateNetworkHost
import com.deepsplat.capture.core.isValidUploadUrl
import com.deepsplat.capture.core.lumaSharpness
import com.deepsplat.capture.core.needsHalfTurn
import com.deepsplat.capture.core.normalizeBackendUrl
import com.deepsplat.capture.core.retryDelaySeconds
import com.deepsplat.capture.core.rotateNv21By180
import com.deepsplat.capture.core.rotated180
import com.deepsplat.capture.core.yuv420ToNv21
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.ByteBuffer
import kotlin.random.Random

class NetworkTest {
    @Test fun privateHosts() {
        for (h in listOf("10.0.0.5", "172.16.1.1", "172.31.255.1", "192.168.1.20", "169.254.3.3", "127.0.0.1", "localhost", "lab.local")) assertTrue(h, isPrivateNetworkHost(h))
        for (h in listOf("172.32.0.1", "8.8.8.8", "example.com", "192.169.0.1", "10.0.0", null)) assertFalse("$h", isPrivateNetworkHost(h))
    }

    @Test fun normalizesWhatPeopleType() {
        assertEquals("http://192.168.1.20:8000", normalizeBackendUrl(" 192.168.1.20:8000/ "))
        assertEquals("https://deepsplat.example.org", normalizeBackendUrl("deepsplat.example.org"))
        assertEquals("http://172.20.10.8:8000", normalizeBackendUrl("http://172.20.10.8:8000"))
        assertNull(normalizeBackendUrl("http://example.org"))          // no plain http on the internet
        assertNull(normalizeBackendUrl("https://user:pw@example.org"))  // no credentials in URLs
        assertNull(normalizeBackendUrl("https://example.org/?x=1"))
        assertNull(normalizeBackendUrl(""))
    }

    @Test fun uploadUrls() {
        assertTrue(isValidUploadUrl("https://storage.googleapis.com/b/k?X-Goog-Signature=abc"))
        assertTrue(isValidUploadUrl("http://192.168.1.20:8000/v1/uploads/uploads/A.zip"))
        assertFalse(isValidUploadUrl("http://storage.example.com/k"))
    }

    @Test fun retryCurveMatchesIos() {
        assertEquals(listOf(2L, 4L, 8L, 16L, 32L, 64L, 128L, 256L, 300L, 300L), (1..10).map { retryDelaySeconds(it) })
    }
}

class GeometryTest {
    private fun project(c2wRowMajor: List<Float>, k: Intrinsics, p: DoubleArray): Pair<Double, Double> {
        val m = c2wRowMajor.map { it.toDouble() }
        // world -> camera (OpenGL axes), then to OpenCV (y down, z forward)
        val d = doubleArrayOf(p[0] - m[3], p[1] - m[7], p[2] - m[11])
        val x = m[0] * d[0] + m[4] * d[1] + m[8] * d[2]
        val y = m[1] * d[0] + m[5] * d[1] + m[9] * d[2]
        val z = m[2] * d[0] + m[6] * d[1] + m[10] * d[2]
        return (k.fx * x / -z + k.cx) to (k.fy * -y / -z + k.cy)
    }

    @Test fun columnMajorToRowMajor() {
        val cm = FloatArray(16) { it.toFloat() }
        val rm = Pose.rowMajor(cm)
        assertEquals(cm[12], rm[3])   // translation x sits at row 0, column 3
        assertEquals(cm[1], rm[4])
    }

    /** Turning image, intrinsics and pose by 180 degrees keeps every 3D point on the turned pixel. */
    @Test fun halfTurnIsConsistent() {
        val k = Intrinsics(1400f, 1390f, 960f, 540f, 1920, 1080)
        val a = Math.toRadians(30.0)
        val pose = listOf(
            Math.cos(a), 0.0, Math.sin(a), 0.4,
            0.0, 1.0, 0.0, 1.5,
            -Math.sin(a), 0.0, Math.cos(a), -2.0,
            0.0, 0.0, 0.0, 1.0,
        ).map { it.toFloat() }
        val p = doubleArrayOf(0.1, 1.3, -5.0)
        val (u, v) = project(pose, k, p)
        val (u2, v2) = project(Pose.rotated180(pose), k.rotated180(), p)
        assertEquals(k.width - u, u2, 1e-3)
        assertEquals(k.height - v, v2, 1e-3)
        assertTrue(needsHalfTurn(270)); assertFalse(needsHalfTurn(90)); assertTrue(needsHalfTurn(-90))
    }
}

class TimingTest {
    @Test fun samplerKeepsTargetRate() {
        val s = FrameSampler(5.0, 30.0)
        val kept = (0 until 300).map { it / 30.0 }.count { s.shouldKeep(it) }
        assertEquals(50, kept) // 10 s at 5 fps
    }

    @Test fun clockShift() {
        assertEquals(0L, clockShiftToBoottime(5_000_000_000, 5_030_000_000, 4_000_000_000))          // already BOOTTIME
        assertEquals(48_970_000_000L, clockShiftToBoottime(1_000_000_000, 50_000_000_000, 1_030_000_000)) // MONOTONIC, phone slept 49 s
        assertEquals(5_030_000_000L - 100_000_000_000_000L, clockShiftToBoottime(100_000_000_000_000, 5_030_000_000, 4_000_000_000)) // unknown
    }
}

class YuvTest {
    @Test fun stridedPlanesToNv21() {
        val w = 6; val h = 4; val yStride = 8; val uvStride = 8
        val r = Random(1)
        val yPlane = ByteArray(yStride * h) { r.nextInt(256).toByte() }
        // Interleaved chroma like most cameras: U plane starts at 0, V at 1, pixel stride 2.
        val uv = ByteArray(uvStride * h / 2) { r.nextInt(256).toByte() }
        val u = ByteBuffer.wrap(uv, 0, uv.size).slice()
        val v = ByteBuffer.wrap(uv, 1, uv.size - 1).slice()
        val out = ByteArray(w * h * 3 / 2)
        yuv420ToNv21(w, h, ByteBuffer.wrap(yPlane), yStride, 1, u, v, uvStride, 2, out)
        for (row in 0 until h) for (c in 0 until w) assertEquals(yPlane[row * yStride + c], out[row * w + c])
        for (row in 0 until h / 2) for (c in 0 until w / 2) {
            val o = w * h + row * w + c * 2
            assertEquals(uv[row * uvStride + c * 2 + 1], out[o])     // V first
            assertEquals(uv[row * uvStride + c * 2], out[o + 1])     // then U
        }
    }

    @Test fun halfTurnTwiceIsIdentity() {
        val w = 8; val h = 6
        val img = ByteArray(w * h * 3 / 2) { it.toByte() }
        val copy = img.copyOf()
        rotateNv21By180(img, w, h)
        assertEquals(copy[0], img[w * h - 1])
        assertEquals(copy[w * h], img[img.size - 2])   // first V lands on the last pair
        rotateNv21By180(img, w, h)
        assertArrayEquals(copy, img)
    }

    @Test fun sharpnessPrefersDetail() {
        val w = 64; val h = 64
        val flat = ByteArray(w * h * 3 / 2) { 128.toByte() }
        val r = Random(3)
        val busy = ByteArray(w * h * 3 / 2) { r.nextInt(256).toByte() }
        assertTrue(lumaSharpness(busy, w, h) > lumaSharpness(flat, w, h) + 100)
    }
}
