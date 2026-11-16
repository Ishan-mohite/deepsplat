package com.deepsplat.capture

import android.app.Application
import android.net.Uri
import com.deepsplat.capture.data.CaptureStore
import com.deepsplat.capture.data.SettingsRepository
import com.deepsplat.capture.data.Uploads
import com.deepsplat.capture.data.VideoImport
import com.deepsplat.capture.data.ZipCaptureImport
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch

class DeepsplatApp : Application() {
    lateinit var graph: AppGraph
        private set

    override fun onCreate() {
        super.onCreate()
        graph = AppGraph(this)
        graph.uploads.restore()
    }
}

class AppGraph(private val app: Application) {
    val settings = SettingsRepository(app)
    val store = CaptureStore(app.filesDir)
    val uploads = Uploads(app, settings, store)

    // Test imports (capture zips from the iPhone app, gallery videos). Run in the app scope so they finish
    // when the person leaves the screen.
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    val importProgress = MutableStateFlow<Float?>(null)
    val importError = MutableStateFlow<String?>(null)

    fun import(uri: Uri, zip: Boolean) {
        if (importProgress.value != null) return
        importProgress.value = 0f
        scope.launch {
            try {
                if (zip) ZipCaptureImport.import(app, this@AppGraph, uri) { importProgress.value = it }
                else VideoImport.import(app, this@AppGraph, uri) { importProgress.value = it }
            } catch (e: Exception) {
                importError.value = e.message ?: "Import failed."
            } finally {
                importProgress.value = null
            }
        }
    }
}
