package com.deepsplat.capture.data

import com.deepsplat.capture.core.CaptureJson
import com.deepsplat.capture.core.CaptureWriter
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import java.io.File

@Serializable
enum class UploadStatus { SAVED, UPLOADING, UPLOADED, FAILED }

@Serializable
enum class UploadStage { WAITING, PREPARING, TRANSFERRING, CONFIRMING }

@Serializable
data class Finding(val type: String, val lengthM: Double? = null, val areaM2: Double? = null, val change: String? = null)

@Serializable
data class CaptureRecord(
    val id: String,
    val mapId: String,
    val mapName: String? = null,
    val createdUnix: Double,
    val frameCount: Int = 0,
    val durationS: Double = 0.0,
    val status: UploadStatus = UploadStatus.SAVED,
    val stage: UploadStage = UploadStage.WAITING,
    val queued: Boolean = false,
    val wifiOnly: Boolean = false,
    val backendUrl: String = "",
    val attempts: Int = 0,
    val error: String? = null,
    val hasUploadedBytes: Boolean = false,
    val uploadedUnix: Double? = null,
    /** App view from GET /v1/captures/{id}: uploaded | processing | done | failed. */
    val backendStatus: String? = null,
    /** Backend status, e.g. done, needs_localization, needs_review. */
    val backendDetail: String? = null,
    val backendError: String? = null,
    val findings: List<Finding>? = null,
    val overlayUrl: String? = null,
) {
    val processingFinished: Boolean
        get() = backendStatus == "failed" || (backendStatus == "done" && backendDetail != "detection_pending")
}

/** The capture list (filesDir/captures.json) plus the capture folders and archives on disk. Thread-safe. */
class CaptureStore(root: File) {
    val capturesDir = File(root, "captures").apply { mkdirs() }
    val archivesDir = File(root, "archives").apply { mkdirs() }
    private val file = File(root, "captures.json")
    private val lock = Any()
    private val serializer = ListSerializer(CaptureRecord.serializer())

    private val _records = MutableStateFlow(load())
    val records: StateFlow<List<CaptureRecord>> = _records.asStateFlow()

    /** Transfer progress 0..1 per capture. In memory only; phase changes are persisted. */
    val progress = MutableStateFlow<Map<String, Float>>(emptyMap())

    var healthy = true
        private set

    private fun load(): List<CaptureRecord> = runCatching {
        if (file.exists()) CaptureJson.decodeFromString(serializer, file.readText()) else emptyList()
    }.getOrElse { healthy = false; emptyList() }

    private fun save(list: List<CaptureRecord>) {
        val tmp = File(file.parentFile, file.name + ".tmp")
        tmp.writeText(CaptureJson.encodeToString(serializer, list))
        if (!tmp.renameTo(file)) throw IllegalStateException("Could not save the capture list.")
    }

    operator fun get(id: String): CaptureRecord? = _records.value.firstOrNull { it.id == id }

    fun folder(id: String) = File(capturesDir, id)
    fun archive(id: String) = File(archivesDir, "$id.zip")
    fun photo(id: String) = File(folder(id), "photo.jpg")

    fun add(record: CaptureRecord) = mutate { list -> listOf(record) + list.filter { it.id != record.id } }

    fun update(id: String, change: (CaptureRecord) -> CaptureRecord) =
        mutate { list -> list.map { if (it.id == id) change(it) else it } }

    fun delete(id: String) {
        mutate { list -> list.filter { it.id != id } }
        folder(id).deleteRecursively()
        archive(id).delete()
        progress.update { it - id }
    }

    private fun mutate(change: (List<CaptureRecord>) -> List<CaptureRecord>) {
        synchronized(lock) {
            val next = change(_records.value).sortedByDescending { it.createdUnix }
            save(next)
            _records.value = next
        }
    }

    /** Captures on disk that the list does not know (e.g. the list was lost) are added back. */
    fun recoverFromDisk() {
        val known = _records.value.map { it.id }.toSet()
        val found = capturesDir.listFiles().orEmpty().filter { it.isDirectory && it.name !in known }.mapNotNull { dir ->
            runCatching { CaptureWriter.readManifest(dir) }.getOrNull()?.let { m ->
                CaptureRecord(m.captureId, m.mapId, null, m.createdUnix, m.video.frames.size, m.video.durationS)
            }
        }
        if (found.isNotEmpty()) mutate { it + found }
    }
}
