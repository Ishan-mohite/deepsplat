package com.deepsplat.capture

import com.deepsplat.capture.core.CaptureException
import com.deepsplat.capture.core.CaptureWriter
import com.deepsplat.capture.core.ZipImport
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

class ZipImportTest {
    @get:Rule val tmp = TemporaryFolder()

    /** Zip the sample capture with or without the <id>/ folder (the iPhone app writes both kinds). */
    private fun zipOf(folder: File, withFolder: Boolean): File {
        val out = File(tmp.root, "in-$withFolder.zip")
        ZipOutputStream(out.outputStream()).use { z ->
            folder.walkTopDown().filter { it.isFile }.forEach { f ->
                val rel = f.relativeTo(folder).invariantSeparatorsPath
                z.putNextEntry(ZipEntry(if (withFolder) "${folder.name}/$rel" else rel)); z.write(f.readBytes()); z.closeEntry()
            }
        }
        return out
    }

    @Test fun importsBothLayouts() {
        for (withFolder in listOf(true, false)) {
            val src = SampleCapture.write(tmp.newFolder())
            val captures = tmp.newFolder()
            val m = ZipImport.unpack(zipOf(src, withFolder).inputStream(), captures)
            val dest = File(captures, m.captureId)
            assertTrue(File(dest, "capture.json").isFile && File(dest, "imu/accel.csv").isFile)
            assertEquals(File(src, "capture.json").readText(), File(dest, "capture.json").readText()) // unchanged
            assertEquals(listOf(m.captureId), captures.list()!!.toList())                            // no temp left
            CaptureWriter.archive(dest, File(tmp.root, "out-$withFolder.zip"))                         // uploadable
        }
    }

    @Test fun rejectsDuplicatesAndNonCaptures() {
        val src = SampleCapture.write(tmp.newFolder())
        val captures = tmp.newFolder()
        ZipImport.unpack(zipOf(src, true).inputStream(), captures)
        try { ZipImport.unpack(zipOf(src, true).inputStream(), captures); throw AssertionError() } catch (e: CaptureException) { assertTrue(e.message!!.contains("already")) }
        val junk = File(tmp.root, "junk.zip")
        ZipOutputStream(junk.outputStream()).use { it.putNextEntry(ZipEntry("a.txt")); it.write(1); it.closeEntry() }
        try { ZipImport.unpack(junk.inputStream(), captures); throw AssertionError() } catch (e: CaptureException) { assertTrue(e.message!!.contains("capture.json")) }
    }

    /** ./gradlew testDebugUnitTest -PimportZip=/path/to/iphone.zip : a real iPhone capture imports and re-zips. */
    @Test fun realIphoneZip() {
        val path = System.getProperty("importZip").orEmpty()
        assumeTrue(path.isNotEmpty() && File(path).isFile)
        val captures = tmp.newFolder()
        val m = ZipImport.unpack(File(path).inputStream(), captures)
        val out = File(System.getProperty("user.dir"), "build/imported/${m.captureId}.zip")
        CaptureWriter.archive(File(captures, m.captureId), out)
        println("imported ${m.captureId}: ${m.video.frames.size} frames, ios ${m.iosVersion}, imu ${m.imu.sampleCounts} -> $out")
    }
}
