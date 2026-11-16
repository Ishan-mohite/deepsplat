package com.deepsplat.capture.data

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Matrix
import android.media.MediaMetadataRetriever
import android.net.Uri
import android.os.Build
import com.deepsplat.capture.AppGraph
import com.deepsplat.capture.BuildConfig
import com.deepsplat.capture.core.CaptureException
import com.deepsplat.capture.core.CaptureManifest
import com.deepsplat.capture.core.CaptureWriter
import com.deepsplat.capture.core.FrameRecord
import com.deepsplat.capture.core.Imu
import com.deepsplat.capture.core.Intrinsics
import com.deepsplat.capture.core.Video
import com.deepsplat.capture.core.lumaSharpness
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import java.io.ByteArrayOutputStream
import java.text.SimpleDateFormat
import java.util.Locale
import java.util.TimeZone
import java.util.UUID
import kotlin.coroutines.coroutineContext

/**
 * Test captures from a gallery video. There is no device tracking, so frames carry no pose and the
 * backend localizes the photo on its own (method vps_photo). Intrinsics are estimated: phone main cameras
 * have fx close to 0.76 x the long image side (Nothing A015: 1464 px at 1920, iPhone: ~1450 at 1920).
 * Images are stored like recorded captures: turning them 90 degrees clockwise gives the upright view.
 */
object VideoImport {
    private const val FOCAL_PER_LONG_SIDE = 0.76f
    private const val MAX_SIDE = 1920

    suspend fun import(context: Context, graph: AppGraph, uri: Uri, onProgress: (Float) -> Unit): String = withContext(Dispatchers.IO) {
        val config = graph.settings.value.capture
        if (config.mapId.isBlank()) throw CaptureException("Choose a site in Settings first.")
        val mmr = MediaMetadataRetriever()
        val id = UUID.randomUUID().toString().uppercase(Locale.ROOT)
        var writer: CaptureWriter? = null
        try {
            mmr.setDataSource(context, uri)
            val durationS = (mmr.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)?.toLongOrNull() ?: 0L) / 1000.0
            if (durationS <= 0) throw CaptureException("This video cannot be read.")
            val rotation = mmr.extractMetadata(MediaMetadataRetriever.METADATA_KEY_VIDEO_ROTATION)?.toIntOrNull() ?: 0
            val vw = mmr.extractMetadata(MediaMetadataRetriever.METADATA_KEY_VIDEO_WIDTH)?.toIntOrNull() ?: 0
            val vh = mmr.extractMetadata(MediaMetadataRetriever.METADATA_KEY_VIDEO_HEIGHT)?.toIntOrNull() ?: 0
            val createdUnix = parseDate(mmr.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DATE)) ?: (System.currentTimeMillis() / 1000.0)
            val length = minOf(durationS, config.maxDuration)
            val times = generateSequence(0.0) { it + 1 / config.fps }.takeWhile { it < length }.toList()

            val w = CaptureWriter(graph.store.capturesDir, id) { 0.0 }
            writer = w
            w.log("Imported from a gallery video: ${"%.1f".format(Locale.ROOT, durationS)} s, ${vw}x$vh, rotation $rotation. No device tracking; intrinsics estimated (fx = $FOCAL_PER_LONG_SIDE x long side).")

            var bestScore = -1.0
            var best: Pair<Bitmap, Double>? = null
            times.forEachIndexed { i, t ->
                coroutineContext.ensureActive()
                val frame = mmr.getFrameAtTime((t * 1_000_000).toLong(), MediaMetadataRetriever.OPTION_CLOSEST) ?: return@forEachIndexed
                val upright = scaled(upright(frame, rotation, vw, vh))
                val (jpeg, k) = stored(upright, config.jpegQuality)
                w.appendFrame(jpeg, FrameRecord("", t, null, k, "portrait", "not_available"))
                val score = sharpness(upright)
                if (score > bestScore) { best?.first?.recycle(); best = upright to t; bestScore = score } else upright.recycle()
                onProgress((i + 1f) / times.size)
            }
            val (photoBmp, photoT) = best ?: throw CaptureException("No frames could be read from this video.")
            val (photoJpeg, pk) = stored(photoBmp, 0.95)
            w.replacePhoto(photoJpeg, FrameRecord("", photoT, null, pk, "portrait", "not_available", false))
            w.log("Photo: sharpest frame at ${"%.2f".format(Locale.ROOT, photoT)} s.")

            w.finish(CaptureManifest(
                captureId = id, mapId = config.mapId, createdUnix = createdUnix, source = "video_import",
                device = "${Build.MANUFACTURER} ${Build.MODEL} (video import)",
                osVersion = "Android ${Build.VERSION.RELEASE} (API ${Build.VERSION.SDK_INT})",
                appVersion = "${BuildConfig.VERSION_NAME} (${BuildConfig.VERSION_CODE})",
                timeBase = "seconds from the start of the video",
                uptimeToUnixOffset = createdUnix,
                video = Video(config.fps, config.fps, length, w.frames.toList()),
                photo = w.photo!!,
                imu = Imu(rateHz = 0.0, sampleCounts = mapOf("accel" to 0, "gyro" to 0, "motion" to 0)),
                geo = null,
            ))
            val s = graph.settings.value
            graph.store.add(CaptureRecord(id, config.mapId, s.mapName.ifBlank { null }, createdUnix, w.frames.size, length))
            if (s.autoUpload) graph.uploads.enqueue(id)
            id
        } catch (e: Throwable) {
            writer?.folder?.deleteRecursively()
            throw if (e is CaptureException || e is kotlinx.coroutines.CancellationException) e else CaptureException("This video could not be imported.")
        } finally {
            mmr.release()
        }
    }

    /** Some devices return frames without the rotation from the video metadata applied. */
    private fun upright(b: Bitmap, rotation: Int, vw: Int, vh: Int): Bitmap {
        val notApplied = (rotation == 90 || rotation == 270) && b.width == vw && b.height == vh && vw != vh ||
            rotation == 180 && b.width == vw && b.height == vh && Build.VERSION.SDK_INT < 28
        if (!notApplied || rotation == 0) return b
        return Bitmap.createBitmap(b, 0, 0, b.width, b.height, Matrix().apply { postRotate(rotation.toFloat()) }, true).also { if (it != b) b.recycle() }
    }

    private fun scaled(b: Bitmap): Bitmap {
        val long = maxOf(b.width, b.height)
        if (long <= MAX_SIDE) return b
        val s = MAX_SIDE.toFloat() / long
        return Bitmap.createScaledBitmap(b, (b.width * s).toInt(), (b.height * s).toInt(), true).also { if (it != b) b.recycle() }
    }

    /** Upright bitmap -> stored JPEG (turned 90 degrees counter-clockwise) and intrinsics for it. */
    private fun stored(upright: Bitmap, quality: Double): Pair<ByteArray, Intrinsics> {
        val raw = Bitmap.createBitmap(upright, 0, 0, upright.width, upright.height, Matrix().apply { postRotate(-90f) }, true)
        val out = ByteArrayOutputStream()
        raw.compress(Bitmap.CompressFormat.JPEG, (quality * 100).toInt(), out)
        val f = FOCAL_PER_LONG_SIDE * maxOf(raw.width, raw.height)
        val k = Intrinsics(f, f, raw.width / 2f, raw.height / 2f, raw.width, raw.height)
        if (raw != upright) raw.recycle()
        return out.toByteArray() to k
    }

    private fun sharpness(b: Bitmap): Double {
        val small = Bitmap.createScaledBitmap(b, 480, (480f * b.height / b.width).toInt().coerceAtLeast(16), true)
        val px = IntArray(small.width * small.height)
        small.getPixels(px, 0, small.width, 0, 0, small.width, small.height)
        val luma = ByteArray(px.size * 3 / 2)
        px.forEachIndexed { i, c -> luma[i] = (((c shr 16 and 0xFF) * 77 + (c shr 8 and 0xFF) * 150 + (c and 0xFF) * 29) shr 8).toByte() }
        val score = lumaSharpness(luma, small.width, small.height, step = 2)
        if (small != b) small.recycle()
        return score
    }

    private fun parseDate(s: String?): Double? = s?.let {
        listOf("yyyyMMdd'T'HHmmss.SSS'Z'", "yyyyMMdd'T'HHmmss'Z'", "yyyyMMdd'T'HHmmss").firstNotNullOfOrNull { p ->
            runCatching { SimpleDateFormat(p, Locale.ROOT).apply { timeZone = TimeZone.getTimeZone("UTC") }.parse(it)!!.time / 1000.0 }.getOrNull()
        }?.takeIf { it > 946_684_800 } // ignore the 1904/1970 placeholders some encoders write
    }
}

/** Capture zips in the iPhone app's format (frames, poses, IMU), e.g. sent from an iPhone for testing. */
object ZipCaptureImport {
    suspend fun import(context: Context, graph: AppGraph, uri: Uri, onProgress: (Float) -> Unit): String = withContext(Dispatchers.IO) {
        val size = context.contentResolver.query(uri, arrayOf(android.provider.OpenableColumns.SIZE), null, null, null)?.use { c ->
            if (c.moveToFirst() && !c.isNull(0)) c.getLong(0) else null
        }
        val input = context.contentResolver.openInputStream(uri) ?: throw CaptureException("The file cannot be opened.")
        val m = try {
            input.use {
                com.deepsplat.capture.core.ZipImport.unpack(it, graph.store.capturesDir) { bytes ->
                    // Uncompressed bytes vs. file size: JPEG zips are close to 1:1.
                    if (size != null && size > 0) onProgress((bytes.toFloat() / size).coerceAtMost(0.99f))
                }
            }
        } catch (e: CaptureException) { throw e } catch (e: java.util.zip.ZipException) {
            throw CaptureException("This file is not a valid zip.")
        } catch (e: kotlinx.serialization.SerializationException) {
            throw CaptureException("capture.json could not be read.")
        }
        // The backend files a capture under the site given at upload, so it lands on the site chosen here.
        val s = graph.settings.value
        val mapId = s.mapId.ifBlank { m.mapId }
        graph.store.add(CaptureRecord(m.captureId, mapId, if (mapId == s.mapId) s.mapName.ifBlank { null } else null,
            m.createdUnix, m.video.frames.size, m.video.durationS))
        if (s.autoUpload) graph.uploads.enqueue(m.captureId)
        onProgress(1f)
        m.captureId
    }
}
