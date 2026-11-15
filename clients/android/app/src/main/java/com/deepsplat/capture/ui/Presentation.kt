package com.deepsplat.capture.ui

import com.deepsplat.capture.data.CaptureRecord
import com.deepsplat.capture.data.Finding
import com.deepsplat.capture.data.UploadStage
import com.deepsplat.capture.data.UploadStatus
import com.deepsplat.capture.ui.components.Tone
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** DESIGN.md: "14 Mar 2026, 10:42". */
fun formatDate(unix: Double): String = SimpleDateFormat("d MMM yyyy, HH:mm", Locale.ENGLISH).format(Date((unix * 1000).toLong()))

fun formatSeconds(s: Double): String = if (s < 10) String.format(Locale.ROOT, "%.1f s", s) else "${s.toInt()} s"

fun clock(s: Double): String {
    val t = s.toInt().coerceAtLeast(0)
    return String.format(Locale.ROOT, "%02d:%02d", t / 60, t % 60)
}

data class StatusView(val text: String, val tone: Tone, val glyph: String?, val detail: String? = null)

enum class Bucket { WAITING, UPLOADED }

fun bucket(r: CaptureRecord) = if (r.status == UploadStatus.UPLOADED) Bucket.UPLOADED else Bucket.WAITING

fun statusOf(r: CaptureRecord, progress: Float?): StatusView = when (r.status) {
    UploadStatus.UPLOADED -> when {
        r.backendStatus == "failed" -> StatusView("Processing failed", Tone.ATTENTION, "!", r.backendError)
        r.backendDetail == "detection_pending" -> StatusView("Waiting for detection", Tone.INFO, "◌",
            "Placed. Damage detection runs as soon as the backend is online again.")
        r.backendDetail == "needs_localization" -> StatusView("Not placed", Tone.WARN, "!",
            "The backend could not find this spot in the scan. Record a slower sweep with more of the room in view.")
        r.backendDetail == "needs_review" -> StatusView("Needs review", Tone.WARN, "!", "Placed with low confidence. Check it in the web app.")
        r.backendDetail == "rejected" -> StatusView("Photo rejected", Tone.WARN, "!", "The photo did not pass the quality check. Record again with a steady, sharp photo.")
        r.backendStatus == "done" -> StatusView("Placed in scan", Tone.LIVE, "✓", "Open the site in the web app to see it in 3D.")
        else -> StatusView("Processing", Tone.INFO, "◌", "Placing the capture in the scan and looking for damage.")
    }
    UploadStatus.UPLOADING -> when (r.stage) {
        UploadStage.TRANSFERRING -> StatusView("Uploading ${((progress ?: 0f) * 100).toInt()} %", Tone.INFO, "↑")
        UploadStage.CONFIRMING -> StatusView("Confirming upload", Tone.INFO, "↑")
        else -> StatusView("Preparing upload", Tone.INFO, "↑")
    }
    UploadStatus.FAILED -> StatusView(if (r.queued) "Upload failed · retrying" else "Upload failed", Tone.ATTENTION, "!", r.error)
    UploadStatus.SAVED -> if (r.queued) StatusView("Waiting for network", Tone.WARN, "…", if (r.wifiOnly) "Uploads wait for Wi-Fi." else "Uploads start when the backend is reachable.")
    else StatusView("Saved on phone", Tone.NEUTRAL, "•", "Not uploaded yet.")
}

fun findingLabel(f: Finding): String {
    val name = f.type.replace('_', ' ').replaceFirstChar { it.titlecase(Locale.ENGLISH) }
    val size = when {
        f.lengthM != null && f.lengthM > 0 -> String.format(Locale.ROOT, "%.2f m", f.lengthM)
        f.areaM2 != null && f.areaM2 > 0 -> String.format(Locale.ROOT, "%.2f m²", f.areaM2)
        else -> null
    }
    return listOfNotNull(name, size, f.change).joinToString(" · ")
}

fun trackingLabel(tracking: String): Pair<String, Tone> = when {
    tracking == "normal" -> "Tracking ready" to Tone.LIVE
    tracking == "limited:initializing" -> "Finding your position" to Tone.WARN
    tracking == "limited:excessive_motion" -> "Move more slowly" to Tone.WARN
    tracking == "limited:insufficient_features" -> "Point at textured surfaces" to Tone.WARN
    tracking == "limited:insufficient_light" -> "More light needed" to Tone.WARN
    tracking.startsWith("limited") -> "Tracking limited" to Tone.WARN
    else -> "Starting camera" to Tone.NEUTRAL
}
