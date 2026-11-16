package com.deepsplat.capture.core

import java.io.File
import java.io.InputStream
import java.util.zip.ZipInputStream

/**
 * Unpacks a capture zip in the iPhone app's format (<capture_id>/capture.json, frames/, photo.jpg, the imu
 * CSV files, log.txt) into [capturesDir]/<capture_id>. The files stay byte for byte as they came, so an
 * upload sends exactly what the iPhone recorded.
 */
object ZipImport {
    const val MAX_BYTES = 500L * 1024 * 1024 // same limit as backend/package.py

    fun unpack(input: InputStream, capturesDir: File, onBytes: (Long) -> Unit = {}): CaptureManifest {
        val tmp = File(capturesDir, ".import-" + System.nanoTime()).apply { mkdirs() }
        try {
            var total = 0L
            ZipInputStream(input.buffered(1 shl 16)).use { zip ->
                val buf = ByteArray(1 shl 16)
                while (true) {
                    val e = zip.nextEntry ?: break
                    val name = e.name.replace('\\', '/')
                    if (name.startsWith("/") || name.split('/').any { it == ".." }) throw CaptureException("The zip contains an unsafe path.")
                    if (name.startsWith("__MACOSX/") || name.substringAfterLast('/').startsWith("._")) continue
                    val target = File(tmp, name)
                    if (e.isDirectory) { target.mkdirs(); continue }
                    target.parentFile?.mkdirs()
                    target.outputStream().use { out ->
                        while (true) {
                            val n = zip.read(buf)
                            if (n < 0) break
                            total += n
                            if (total > MAX_BYTES) throw CaptureException("The zip is larger than 500 MB.")
                            out.write(buf, 0, n)
                        }
                    }
                    onBytes(total)
                }
            }
            val manifestFile = tmp.walkTopDown().filter { it.isFile && it.name == "capture.json" }.minByOrNull { it.path.length }
                ?: throw CaptureException("This zip is not a capture: capture.json is missing.")
            val root = manifestFile.parentFile!!
            val m = CaptureWriter.readManifest(root)
            if (m.captureId.isBlank() || m.captureId.contains('/') || m.captureId.contains("..")) throw CaptureException("The capture ID is invalid.")
            val dest = File(capturesDir, m.captureId)
            if (dest.exists()) throw CaptureException("This capture is already on the phone.")
            if (!root.renameTo(dest)) throw CaptureException("The capture could not be saved.")
            return m
        } finally {
            tmp.deleteRecursively()
        }
    }
}
