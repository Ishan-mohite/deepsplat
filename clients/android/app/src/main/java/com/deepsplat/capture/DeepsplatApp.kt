package com.deepsplat.capture

import android.app.Application
import com.deepsplat.capture.data.CaptureStore
import com.deepsplat.capture.data.SettingsRepository
import com.deepsplat.capture.data.Uploads

class DeepsplatApp : Application() {
    lateinit var graph: AppGraph
        private set

    override fun onCreate() {
        super.onCreate()
        graph = AppGraph(this)
        graph.uploads.restore()
    }
}

class AppGraph(app: Application) {
    val settings = SettingsRepository(app)
    val store = CaptureStore(app.filesDir)
    val uploads = Uploads(app, settings, store)
}
