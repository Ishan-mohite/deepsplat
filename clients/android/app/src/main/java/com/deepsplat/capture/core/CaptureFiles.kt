package com.deepsplat.capture.core

import java.io.BufferedWriter
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.util.zip.CRC32
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

object ImuCsv {
    const val ACCEL_HEADER = "t,ax,ay,az\n"
    const val GYRO_HEADER = "t,gx,gy,gz\n"
    const val MOTION_HEADER = "t,qx,qy,qz,qw,grav_x,grav_y,grav_z,uacc_x,uacc_y,uacc_z,rot_x,rot_y,rot_z\n"
}

/** Buffered CSV appender. Confine an instance to one thread. */
class CsvWriter(file: File, header: String) {
    private val out: BufferedWriter = file.bufferedWriter().also { it.write(header) }
    var count = 0
        private set

    fun append(values: DoubleArray) {
        if (values.any { !it.isFinite() }) return // drop the sample rather than the capture
        val sb = StringBuilder(values.size * 12)
        values.forEachIndexed { i, v -> if (i > 0) sb.append(','); sb.append(v) }
        sb.append('\n')
        out.write(sb.toString())
        count++
    }

    fun close() {
        out.flush()
        out.close()
    }
}

/** One capture folder: frames/, photo.jpg, the imu CSV files, log.txt, capture.json. Confine to one thread. */
class CaptureWriter(root: File, val id: String, private val clock: () -> Double) {
    val folder = File(root, id)
    val frames = mutableListOf<FrameRecord>()
    var photo: FrameRecord? = null
        private set

    init {
        if (folder.exists()) throw CaptureException("Capture already exists.")
        File(folder, "frames").mkdirs()
        File(folder, "imu").mkdirs()
        File(folder, "log.txt").writeText("")
    }

    fun appendFrame(jpeg: ByteArray, record: FrameRecord): FrameRecord {
        val r = record.copy(file = "frames/%06d.jpg".format(frames.size + 1))
        writeAtomic(File(folder, r.file), jpeg)
        frames += r
        return r
    }

    fun replacePhoto(jpeg: ByteArray, record: FrameRecord) {
        val r = record.copy(file = "photo.jpg")
        writeAtomic(File(folder, r.file), jpeg)
        photo = r
    }

    fun log(text: String) {
        runCatching { File(folder, "log.txt").appendText("[%.3f] %s\n".format(java.util.Locale.ROOT, clock(), text)) }
    }

    fun finish(manifest: CaptureManifest) {
        manifest.validate()
        if (manifest.captureId != id) throw CaptureException("Capture ID mismatch.")
        validateContents(manifest, folder)
        writeAtomic(File(folder, "capture.json"), CaptureJson.encodeToString(CaptureManifest.serializer(), manifest).toByteArray())
        log("Saved ${frames.size} raw frames and one defect photo.")
    }

    companion object {
        fun readManifest(folder: File): CaptureManifest {
            val m = CaptureJson.decodeFromString(CaptureManifest.serializer(), File(folder, "capture.json").readText())
            m.validate()
            validateContents(m, folder)
            return m
        }

        private fun validateContents(m: CaptureManifest, folder: File) {
            for (f in m.referencedFiles) {
                if (f.contains("..") || f.startsWith("/") || f.contains("\\") || !File(folder, f).isFile) {
                    throw CaptureException("A referenced capture file is missing or invalid: $f")
                }
            }
        }

        private fun writeAtomic(target: File, bytes: ByteArray) {
            val tmp = File(target.parentFile, target.name + ".part")
            FileOutputStream(tmp).use { it.write(bytes); it.fd.sync() }
            if (!tmp.renameTo(target)) throw CaptureException("Could not write ${target.name}.")
        }

        /**
         * Zips the folder as <id>/... like the iPhone app. Entries are STORED: the payload is JPEG, so
         * deflate saves almost nothing and costs seconds on a phone.
         */
        fun archive(folder: File, destination: File) {
            readManifest(folder)
            destination.parentFile?.mkdirs()
            val partial = File(destination.parentFile, destination.name + ".partial")
            val files = folder.walkTopDown().filter { it.isFile && !it.name.endsWith(".part") }.sortedBy { it.path }.toList()
            ZipOutputStream(FileOutputStream(partial).buffered(1 shl 16)).use { zip ->
                zip.setMethod(ZipOutputStream.STORED)
                val buf = ByteArray(1 shl 16)
                for (f in files) {
                    val crc = CRC32()
                    FileInputStream(f).use { inp -> while (true) { val n = inp.read(buf); if (n < 0) break; crc.update(buf, 0, n) } }
                    val name = folder.name + "/" + f.relativeTo(folder).invariantSeparatorsPath
                    zip.putNextEntry(ZipEntry(name).apply {
                        method = ZipEntry.STORED; size = f.length(); compressedSize = f.length(); this.crc = crc.value
                        time = f.lastModified()
                    })
                    FileInputStream(f).use { inp -> while (true) { val n = inp.read(buf); if (n < 0) break; zip.write(buf, 0, n) } }
                    zip.closeEntry()
                }
            }
            if (destination.exists()) destination.delete()
            if (!partial.renameTo(destination)) throw CaptureException("The capture could not be zipped.")
        }
    }
}
