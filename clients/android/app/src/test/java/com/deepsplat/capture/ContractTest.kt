package com.deepsplat.capture

import com.deepsplat.capture.core.CaptureJson
import com.deepsplat.capture.core.CaptureManifest
import com.deepsplat.capture.core.CaptureWriter
import com.deepsplat.capture.core.CsvWriter
import com.deepsplat.capture.core.FrameRecord
import com.deepsplat.capture.core.ImuCsv
import com.deepsplat.capture.core.Imu
import com.deepsplat.capture.core.Intrinsics
import com.deepsplat.capture.core.Video
import com.deepsplat.capture.data.BackendClient
import com.deepsplat.capture.data.BackendException
import com.deepsplat.capture.data.CaptureRecord
import com.deepsplat.capture.data.CaptureStore
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.util.zip.ZipFile
import kotlin.math.cos
import kotlin.math.sin

/** A capture written the way the app writes it. Also used by the backend check (scripts/check-capture.sh). */
object SampleCapture {
    /** A real 640x480 JPEG (test resource); the backend decodes and rotates it. */
    private val frameJpeg: ByteArray by lazy { SampleCapture::class.java.getResourceAsStream("/frame.jpg")!!.readBytes() }
    @Suppress("UNUSED_PARAMETER") fun jpeg(w: Int, h: Int, seed: Int): ByteArray = frameJpeg

    fun write(root: File, id: String = "6F1C2A9E-3B4D-4E8F-9A0B-1C2D3E4F5A6B", frames: Int = 5): File {
        val w = CaptureWriter(root, id) { 100.0 }
        val k = Intrinsics(500f, 500f, 320f, 240f, 640, 480)
        fun pose(i: Int): List<Float> {
            val a = 0.1 * i
            return listOf(cos(a), 0.0, sin(a), 0.2 * i, 0.0, 1.0, 0.0, 1.4, -sin(a), 0.0, cos(a), 0.0, 0.0, 0.0, 0.0, 1.0).map { it.toFloat() }
        }
        repeat(frames) { i -> w.appendFrame(jpeg(640, 480, i), FrameRecord("", 100.0 + 0.2 * i, pose(i), k, "portrait", "normal")) }
        w.replacePhoto(jpeg(640, 480, 99), FrameRecord("", 100.5, pose(2), k, "portrait", "normal", false))
        val counts = mapOf("accel" to 3, "gyro" to 3, "motion" to 3)
        for ((name, header) in listOf("accel" to ImuCsv.ACCEL_HEADER, "gyro" to ImuCsv.GYRO_HEADER, "motion" to ImuCsv.MOTION_HEADER)) {
            val c = CsvWriter(File(w.folder, "imu/$name.csv"), header)
            repeat(3) { c.append(DoubleArray(if (name == "motion") 14 else 4) { j -> 100.0 + it * 0.01 + j }) }
            c.close()
        }
        w.finish(CaptureManifest(
            captureId = id, mapId = "auditorium", createdUnix = 1_794_651_300.0, device = "Test Phone",
            osVersion = "Android 15 (API 35)", appVersion = "1.0.0 (1)", uptimeToUnixOffset = 1_794_651_200.0,
            video = Video(5.0, 30.0, 0.8, w.frames.toList()), photo = w.photo!!,
            imu = Imu(rateHz = 100.0, sampleCounts = counts), geo = null,
        ))
        return w.folder
    }
}

class CaptureFilesTest {
    @get:Rule val tmp = TemporaryFolder()

    @Test fun manifestMatchesIosSchema() {
        val folder = SampleCapture.write(tmp.root)
        val json = Json.parseToJsonElement(File(folder, "capture.json").readText()).jsonObject
        for (key in listOf("schema_version", "capture_id", "map_id", "created_unix", "time_base", "uptime_to_unix_offset", "conventions", "video", "photo", "imu", "geo")) {
            assertTrue("missing $key", key in json)
        }
        assertEquals("null", json["geo"].toString())                      // explicit null like iOS
        val photo = json["photo"]!!.jsonObject
        assertEquals("photo.jpg", photo["file"]!!.jsonPrimitive.content)
        assertEquals("portrait", photo["interface_orientation"]!!.jsonPrimitive.content)
        assertEquals("false", photo["high_res"].toString())
        val frame0 = json["video"]!!.jsonObject["frames"].toString()
        assertTrue(frame0.contains("\"frames/000001.jpg\"") && frame0.contains("camera_to_world"))
        CaptureWriter.readManifest(folder)                                  // round trip and file check
    }

    @Test fun zipHasCaptureFolderAndStoredEntries() {
        val folder = SampleCapture.write(tmp.root)
        val zip = File(tmp.root, "out/a.zip")
        CaptureWriter.archive(folder, zip)
        ZipFile(zip).use { z ->
            val names = z.entries().toList().map { it.name }.toSet()
            val id = folder.name
            for (n in listOf("capture.json", "photo.jpg", "frames/000001.jpg", "frames/000005.jpg", "imu/accel.csv", "imu/gyro.csv", "imu/motion.csv", "log.txt")) {
                assertTrue("missing $n", "$id/$n" in names)
            }
            assertTrue(z.entries().toList().all { it.method == java.util.zip.ZipEntry.STORED })
        }
    }

    @Test fun missingPhotoIsRejected() {
        val w = CaptureWriter(tmp.root, "X") { 0.0 }
        val k = Intrinsics(1f, 1f, 0f, 0f, 1, 1)
        val f = w.appendFrame(byteArrayOf(1), FrameRecord("", 1.0, List(16) { if (it % 5 == 0) 1f else 0f }, k, "portrait", "normal"))
        try {
            w.finish(CaptureManifest(captureId = "X", mapId = "m", createdUnix = 1.0, device = "d", osVersion = "o", appVersion = "a",
                uptimeToUnixOffset = 0.0, video = Video(5.0, 30.0, 1.0, listOf(f)), photo = f, imu = Imu(rateHz = 100.0, sampleCounts = emptyMap()), geo = null))
            fail("accepted a capture without photo")
        } catch (e: Exception) { assertTrue(e.message!!.contains("photo")) }
    }

    @Test fun storeSurvivesRestartAndRecovers() {
        val store = CaptureStore(tmp.root)
        store.add(CaptureRecord("A", "auditorium", "Auditorium", 10.0, 3, 2.0))
        assertEquals("Auditorium", CaptureStore(tmp.root)["A"]?.mapName)
        SampleCapture.write(store.capturesDir, "B")
        val again = CaptureStore(tmp.root)
        again.recoverFromDisk()
        assertEquals(setOf("A", "B"), again.records.value.map { it.id }.toSet())
        again.delete("B")
        assertFalse(again.folder("B").exists())
    }
}

class BackendClientTest {
    private fun server() = MockWebServer().apply { start() }

    @Test fun fullUploadFlow() {
        val s = server()
        val base = s.url("/").toString().trimEnd('/')
        s.enqueue(MockResponse().setBody("""{"capture_id":"A","upload_url":"${base}/v1/uploads/uploads/A.zip","expires_at":"2026-11-16T10:00:00Z"}"""))
        s.enqueue(MockResponse().setBody("""{"size_bytes":4}"""))
        s.enqueue(MockResponse().setResponseCode(202).setBody("""{"status":"processing"}"""))
        s.enqueue(MockResponse().setBody("""{"capture_id":"A","status":"done","detail":"needs_localization","error":null}"""))
        val c = BackendClient(base, "1234")
        val t = c.createUpload("A", "auditorium", 4, 1.5, "android-1", "M. Keller")
        val create = s.takeRequest()
        assertEquals("/v1/captures", create.path)
        assertEquals("Bearer 1234", create.getHeader("Authorization"))
        val body = CaptureJson.parseToJsonElement(create.body.readUtf8()).jsonObject
        assertEquals("auditorium", body["map_id"]!!.jsonPrimitive.content)
        assertEquals("4", body["size_bytes"].toString())

        val zip = File.createTempFile("cap", ".zip").apply { writeBytes(byteArrayOf(1, 2, 3, 4)) }
        var last = 0f
        c.upload(t.uploadUrl, zip) { last = it }
        val put = s.takeRequest()
        assertEquals("PUT", put.method)
        assertEquals("application/zip", put.getHeader("Content-Type"))
        assertNull("token must not go to storage", put.getHeader("Authorization"))
        assertEquals(1f, last)

        assertEquals("processing", c.complete("A").status)
        assertEquals("/v1/captures/A/complete", s.takeRequest().path)
        assertEquals("needs_localization", c.status("A").detail)
        s.shutdown()
    }

    @Test fun tokenCheck() {
        val s = server()
        s.enqueue(MockResponse().setResponseCode(404))
        s.enqueue(MockResponse().setResponseCode(401))
        val c = BackendClient(s.url("/").toString(), "t")
        assertTrue(c.tokenAccepted())
        assertFalse(c.tokenAccepted())
        s.shutdown()
    }

    @Test fun rejectedTokenIsExplained() {
        val s = server()
        s.enqueue(MockResponse().setResponseCode(401))
        try { BackendClient(s.url("/").toString(), "bad").status("A"); fail() } catch (e: BackendException) {
            assertEquals(401, e.code); assertTrue(e.message!!.contains("token"))
        }
        s.shutdown()
    }

    @Test fun loopbackUploadUrlPointsAtBackend() {
        val c = BackendClient("http://192.168.1.20:8000", "t")
        assertEquals("http://192.168.1.20:8000/v1/uploads/uploads/A.zip", c.reachableUploadUrl("http://localhost:8000/v1/uploads/uploads/A.zip"))
        val signed = "https://storage.googleapis.com/b/uploads/A.zip?X-Goog-Signature=abc"
        assertEquals(signed, c.reachableUploadUrl(signed))
    }

    @Test fun mapsList() {
        val s = server()
        s.enqueue(MockResponse().setBody("""[{"id":"auditorium","name":"Auditorium","status":"ready","vps_status":"ready","property":null,"floor_label":null,"extra":1}]"""))
        val m = BackendClient(s.url("/").toString(), "").maps()
        assertEquals("ready", m.single().vpsStatus)
        s.shutdown()
    }
}

/** Writes a sample zip to build/sample-capture for scripts/check-capture.sh (backend parser check). */
class SampleExport {
    @Test fun export() {
        val out = File(System.getProperty("user.dir"), "build/sample-capture").apply { deleteRecursively(); mkdirs() }
        val folder = SampleCapture.write(File(out, "src"))
        CaptureWriter.archive(folder, File(out, "capture.zip"))
    }
}
