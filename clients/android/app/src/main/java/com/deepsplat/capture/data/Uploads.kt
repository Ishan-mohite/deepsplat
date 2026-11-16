package com.deepsplat.capture.data

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.pm.ServiceInfo
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.work.BackoffPolicy
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.ForegroundInfo
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.OutOfQuotaPolicy
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import com.deepsplat.capture.DeepsplatApp
import com.deepsplat.capture.R
import com.deepsplat.capture.core.CaptureWriter
import com.deepsplat.capture.core.normalizeBackendUrl
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.util.UUID
import java.util.concurrent.TimeUnit

/** Queues captures for upload (WorkManager, survives app restarts) and follows backend processing. */
class Uploads(private val context: Context, private val settings: SettingsRepository, private val store: CaptureStore) {
    private val work = WorkManager.getInstance(context)

    val deviceId: String by lazy {
        val prefs = context.getSharedPreferences("device", Context.MODE_PRIVATE)
        prefs.getString("id", null) ?: ("android-" + UUID.randomUUID().toString().take(8)).also { prefs.edit().putString("id", it).apply() }
    }

    /** Returns an error message for the person, or null when queued. */
    fun enqueue(id: String): String? {
        val record = store[id] ?: return "Capture not found."
        if (record.status == UploadStatus.UPLOADED) return null
        val s = settings.value
        val backend = normalizeBackendUrl(s.backendUrl) ?: return "Add your backend address in Settings first."
        if (settings.token(backend).isEmpty()) return "Add the backend token in Settings first."
        store.update(id) {
            it.copy(queued = true, backendUrl = if (it.hasUploadedBytes && it.backendUrl.isNotEmpty()) it.backendUrl else backend,
                wifiOnly = s.wifiOnly, error = null, status = if (it.status == UploadStatus.FAILED) UploadStatus.SAVED else it.status)
        }
        schedule(id, ExistingWorkPolicy.KEEP)
        return null
    }

    private fun schedule(id: String, policy: ExistingWorkPolicy) {
        // No network constraint: JobScheduler only counts networks with verified internet, so uploads to a
        // backend on an offline Wi-Fi would never start. The worker retries on its own instead.
        val request = OneTimeWorkRequestBuilder<UploadWorker>()
            .setInputData(workDataOf("id" to id))
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 10, TimeUnit.SECONDS)
            .setExpedited(OutOfQuotaPolicy.RUN_AS_NON_EXPEDITED_WORK_REQUEST)
            .addTag(TAG)
            .build()
        work.enqueueUniqueWork("upload-$id", policy, request)
    }

    /** Waiting or failed uploads start again now (backend came back, Wi-Fi joined) instead of after their back-off. */
    fun retryNow() {
        for (r in store.records.value) {
            if (r.queued && r.status != UploadStatus.UPLOADED && r.status != UploadStatus.UPLOADING) schedule(r.id, ExistingWorkPolicy.REPLACE)
        }
    }

    private fun watchNetworks() {
        val cm = context.getSystemService(ConnectivityManager::class.java) ?: return
        val request = NetworkRequest.Builder()
            .removeCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
            .addTransportType(NetworkCapabilities.TRANSPORT_WIFI)
            .addTransportType(NetworkCapabilities.TRANSPORT_ETHERNET)
            .build()
        runCatching {
            cm.registerNetworkCallback(request, object : ConnectivityManager.NetworkCallback() {
                override fun onAvailable(network: Network) = retryNow()
            })
        }
    }

    fun cancel(id: String) {
        work.cancelUniqueWork("upload-$id")
        store.update(id) { if (it.status == UploadStatus.UPLOADED) it else it.copy(queued = false, status = UploadStatus.SAVED, stage = UploadStage.WAITING) }
        store.progress.update { it - id }
    }

    /** After a restart: records stuck in "uploading" whose work is gone go back to the queue. */
    fun restore() {
        store.recoverFromDisk()
        for (r in store.records.value) {
            if (r.queued && r.status != UploadStatus.UPLOADED) schedule(r.id, ExistingWorkPolicy.KEEP)
        }
        watchNetworks()
    }

    /** Polls processing status while [scope] is alive (the app is on screen). */
    fun followProcessing(scope: CoroutineScope) = scope.launch(Dispatchers.IO) {
        while (isActive) {
            val pending = store.records.value.filter { it.status == UploadStatus.UPLOADED && !it.processingFinished && it.backendUrl.isNotEmpty() }
            for (r in pending) refresh(r.id)
            delay(if (pending.isEmpty()) 15_000 else 4_000)
        }
    }

    suspend fun refresh(id: String) = withContext(Dispatchers.IO) {
        val r = store[id] ?: return@withContext
        if (r.status != UploadStatus.UPLOADED || r.backendUrl.isEmpty()) return@withContext
        runCatching {
            val client = BackendClient(r.backendUrl, settings.token(r.backendUrl))
            val st = client.status(id)
            val result = if (st.status == "done") runCatching { client.result(id) }.getOrNull() else null
            store.update(id) {
                it.copy(backendStatus = st.status, backendDetail = st.detail, backendError = st.error,
                    findings = result?.findings ?: it.findings, overlayUrl = result?.overlayUrl ?: it.overlayUrl)
            }
        }
    }

    companion object {
        const val TAG = "capture-upload"
        const val CHANNEL = "uploads"
    }
}

class UploadWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    private val graph = (context.applicationContext as DeepsplatApp).graph
    private val store = graph.store
    private val id = inputData.getString("id").orEmpty()

    override suspend fun getForegroundInfo(): ForegroundInfo = foreground(0f)

    private fun foreground(progress: Float): ForegroundInfo {
        val nm = applicationContext.getSystemService(NotificationManager::class.java)
        if (nm.getNotificationChannel(Uploads.CHANNEL) == null) {
            nm.createNotificationChannel(NotificationChannel(Uploads.CHANNEL, applicationContext.getString(R.string.upload_channel), NotificationManager.IMPORTANCE_LOW).apply {
                description = applicationContext.getString(R.string.upload_channel_description)
            })
        }
        val n = NotificationCompat.Builder(applicationContext, Uploads.CHANNEL)
            .setSmallIcon(R.drawable.ic_launcher_monochrome)
            .setContentTitle("Uploading capture")
            .setContentText(store[id]?.mapName ?: store[id]?.mapId ?: "")
            .setOngoing(true).setOnlyAlertOnce(true).setSilent(true)
            .setProgress(100, (progress * 100).toInt(), progress <= 0f)
            .build()
        val nid = id.hashCode()
        return if (Build.VERSION.SDK_INT >= 29) ForegroundInfo(nid, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC) else ForegroundInfo(nid, n)
    }

    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        val record = store[id] ?: return@withContext Result.success()
        if (record.status == UploadStatus.UPLOADED || !record.queued) return@withContext Result.success()
        if (record.wifiOnly && !LocalNetwork.hasWifi()) {
            store.update(id) { it.copy(status = UploadStatus.SAVED, stage = UploadStage.WAITING) }
            return@withContext Result.retry()
        }
        val token = graph.settings.token(record.backendUrl)
        store.update(id) { it.copy(status = UploadStatus.UPLOADING, stage = UploadStage.PREPARING, error = null, attempts = it.attempts + 1) }
        try {
            val client = BackendClient(record.backendUrl, token)
            if (!record.hasUploadedBytes) {
                val zip = store.archive(id)
                if (!zip.exists()) CaptureWriter.archive(store.folder(id), zip)
                val ticket = client.createUpload(id, record.mapId, zip.length(), record.createdUnix, graph.uploads.deviceId, graph.settings.value.recorder)
                store.update(id) { it.copy(stage = UploadStage.TRANSFERRING) }
                runCatching { setForeground(foreground(0f)) }
                var lastNotified = 0f
                client.upload(ticket.uploadUrl, zip) { p ->
                    store.progress.update { it + (id to p) }
                    if (p - lastNotified >= 0.05f) { lastNotified = p; runCatching { setForegroundAsync(foreground(p)) } }
                }
                store.update(id) { it.copy(hasUploadedBytes = true, stage = UploadStage.CONFIRMING) }
            }
            store.update(id) { it.copy(stage = UploadStage.CONFIRMING) }
            val status = client.complete(id)
            store.update(id) {
                it.copy(status = UploadStatus.UPLOADED, stage = UploadStage.WAITING, queued = false, error = null,
                    backendStatus = status.status, uploadedUnix = System.currentTimeMillis() / 1000.0)
            }
            store.progress.update { it - id }
            store.archive(id).delete() // the folder stays; the zip is rebuilt on demand
            Result.success()
        } catch (e: Exception) {
            val auth = (e as? BackendException)?.code in setOf(401, 403)
            val expired = (e as? BackendException)?.code in setOf(404, 409) && record.hasUploadedBytes
            store.update(id) {
                it.copy(status = UploadStatus.FAILED, stage = UploadStage.WAITING, queued = !auth,
                    hasUploadedBytes = it.hasUploadedBytes && !expired,
                    error = friendly(e))
            }
            store.progress.update { it - id }
            if (auth) Result.failure() else Result.retry()
        }
    }

    private fun friendly(e: Exception): String = when (e) {
        is BackendException -> e.message ?: "Upload failed."
        is java.net.UnknownHostException, is java.net.ConnectException, is java.net.NoRouteToHostException ->
            "The backend cannot be reached from this network. Retrying automatically."
        is java.net.SocketTimeoutException -> "The connection timed out. Retrying automatically."
        else -> e.message ?: "Upload failed. Retrying automatically."
    }
}
