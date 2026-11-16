package com.deepsplat.capture

import android.app.Application
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onRoot
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.work.testing.WorkManagerTestInitHelper
import com.deepsplat.capture.capture.CaptureUi
import com.deepsplat.capture.capture.Phase
import com.deepsplat.capture.data.CaptureRecord
import com.deepsplat.capture.data.Finding
import com.deepsplat.capture.data.UploadStage
import com.deepsplat.capture.data.UploadStatus
import com.deepsplat.capture.ui.screens.CaptureScreen
import com.deepsplat.capture.ui.screens.HomeScreen
import com.deepsplat.capture.ui.screens.RecordOverlay
import com.deepsplat.capture.ui.screens.SettingsScreen
import com.deepsplat.capture.ui.theme.DeepsplatTheme
import com.deepsplat.capture.ui.theme.Ds
import com.github.takahirom.roborazzi.captureRoboImage
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import java.io.File

/** UI screenshots without a phone: ./gradlew testDebugUnitTest -Pscreens writes PNGs to app/build/screens */
@RunWith(AndroidJUnit4::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [35], qualifiers = "w393dp-h851dp-xxhdpi", application = Application::class)
class ScreensTest {
    @get:Rule val compose = createComposeRule()
    private lateinit var graph: AppGraph
    private val out = File(System.getProperty("user.dir"), "build/screens").apply { mkdirs() }

    @Before fun setUp() {
        assumeTrue(System.getProperty("screens") == "true")
        val app = ApplicationProvider.getApplicationContext<Application>()
        WorkManagerTestInitHelper.initializeTestWorkManager(app)
        graph = AppGraph(app)
        graph.settings.update { it.copy(backendUrl = "http://127.0.0.1:9", mapId = "auditorium", mapName = "Auditorium") }
        val jpeg = javaClass.getResourceAsStream("/frame.jpg")!!.readBytes()
        val now = 1_794_960_000.0
        listOf(
            CaptureRecord("A", "auditorium", "Auditorium", now, 99, 20.0, UploadStatus.UPLOADED, backendStatus = "done", backendDetail = "done",
                findings = listOf(Finding("crack", lengthM = 0.42, change = "new"), Finding("stain", areaM2 = 0.08))),
            CaptureRecord("B", "auditorium", "Auditorium", now - 600, 80, 16.0, UploadStatus.UPLOADING, UploadStage.TRANSFERRING, queued = true),
            CaptureRecord("C", "auditorium", "Auditorium", now - 3600, 99, 20.0, UploadStatus.UPLOADED, backendStatus = "done", backendDetail = "needs_localization"),
            CaptureRecord("D", "auditorium", "Auditorium", now - 7200, 60, 12.0, UploadStatus.FAILED, queued = true,
                error = "The backend cannot be reached from this network. Retrying automatically."),
        ).forEach { r ->
            graph.store.add(r)
            graph.store.folder(r.id).mkdirs()
            graph.store.photo(r.id).writeBytes(jpeg)
        }
        graph.store.progress.value = mapOf("B" to 0.62f)
    }

    private fun shot(name: String) = compose.onRoot().captureRoboImage(File(out, "$name.png").path)

    @Test fun home() {
        compose.setContent { DeepsplatTheme { HomeScreen(graph, {}, {}, {}) } }
        compose.waitForIdle(); shot("1-home")
    }

    @Test fun capture() {
        compose.setContent { DeepsplatTheme { CaptureScreen(graph, "A", {}) } }
        compose.waitForIdle(); shot("3-capture")
    }

    @Test fun captureFailed() {
        compose.setContent { DeepsplatTheme { CaptureScreen(graph, "D", {}) } }
        compose.waitForIdle(); shot("4-capture-failed")
    }

    @Test fun settings() {
        compose.setContent { DeepsplatTheme { SettingsScreen(graph, {}) } }
        compose.waitForIdle(); shot("5-settings")
    }

    @Test fun recordIdle() {
        compose.setContent { DeepsplatTheme { Box(Modifier.fillMaxSize().background(Ds.vp2)) { RecordOverlay(CaptureUi(phase = Phase.READY, tracking = "normal"), 20.0, {}, {}, {}, {}) } } }
        compose.waitForIdle(); shot("2a-record-ready")
    }

    @Test fun recordRunning() {
        compose.setContent { DeepsplatTheme { Box(Modifier.fillMaxSize().background(Ds.vp2)) { RecordOverlay(CaptureUi(phase = Phase.RECORDING, tracking = "normal", elapsed = 8.4, frames = 42), 20.0, {}, {}, {}, {}) } } }
        compose.waitForIdle(); shot("2b-record-running")
    }

    @Test fun recordLimited() {
        compose.setContent { DeepsplatTheme { Box(Modifier.fillMaxSize().background(Ds.vp2)) { RecordOverlay(CaptureUi(phase = Phase.RECORDING, tracking = "limited:excessive_motion", elapsed = 12.0, hasPhoto = true), 20.0, {}, {}, {}, {}) } } }
        compose.waitForIdle(); shot("2c-record-limited")
    }
}
