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

fun clock(s: Double): String {
    val t = s.toInt().coerceAtLeast(0)
    return String.format(Locale.ROOT, "%02d:%02d", t / 60, t % 60)
}

data class StatusView(val text: String, val tone: Tone, val glyph: String?, val detail: String? = null)

fun statusOf(r: CaptureRecord, progress: Float?): StatusView = when (r.status) {
    UploadStatus.UPLOADED -> when {
        r.backendStatus == "failed" -> StatusView("Processing failed", Tone.ATTENTION, "!", r.backendError)
        r.backendDetail == "detection_pending" -> StatusView("Waiting for detection", Tone.INFO, "◌")
        r.backendDetail == "needs_localization" -> StatusView("Not placed", Tone.WARN, "!")
        r.backendDetail == "needs_review" -> StatusView("Needs review", Tone.WARN, "!")
        r.backendDetail == "rejected" -> StatusView("Photo rejected", Tone.WARN, "!")
        r.backendStatus == "done" -> StatusView("Placed", Tone.LIVE, "✓")
        else -> StatusView("Processing", Tone.INFO, "◌")
    }
    UploadStatus.UPLOADING -> when (r.stage) {
        UploadStage.TRANSFERRING -> StatusView("Uploading ${((progress ?: 0f) * 100).toInt()} %", Tone.INFO, "↑")
        else -> StatusView("Uploading", Tone.INFO, "↑")
    }
    UploadStatus.FAILED -> StatusView(if (r.queued) "Upload failed · retrying" else "Upload failed", Tone.ATTENTION, "!", r.error)
    UploadStatus.SAVED -> if (r.queued) StatusView(if (r.wifiOnly) "Waiting for Wi-Fi" else "Waiting for network", Tone.WARN, "…")
    else StatusView("Not uploaded", Tone.NEUTRAL, "•")
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
