package com.deepsplat.capture.ui.screens

import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.CloudUpload
import androidx.compose.material.icons.outlined.DeleteOutline
import androidx.compose.material.icons.outlined.IosShare
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.core.content.FileProvider
import com.deepsplat.capture.AppGraph
import com.deepsplat.capture.core.CaptureWriter
import com.deepsplat.capture.data.BackendClient
import com.deepsplat.capture.data.UploadStage
import com.deepsplat.capture.data.UploadStatus
import com.deepsplat.capture.ui.components.BtnStyle
import com.deepsplat.capture.ui.components.CapturePhoto
import com.deepsplat.capture.ui.components.Chip
import com.deepsplat.capture.ui.components.DsButton
import com.deepsplat.capture.ui.components.RoundIconButton
import com.deepsplat.capture.ui.components.Tone
import com.deepsplat.capture.ui.findingLabel
import com.deepsplat.capture.ui.formatDate
import com.deepsplat.capture.ui.statusOf
import com.deepsplat.capture.ui.theme.Ds
import com.deepsplat.capture.ui.theme.DsType
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun CaptureScreen(graph: AppGraph, id: String, onBack: () -> Unit) {
    val context = LocalContext.current
    val records by graph.store.records.collectAsState()
    val progress by graph.store.progress.collectAsState()
    val record = records.firstOrNull { it.id == id }
    val scope = rememberCoroutineScope()
    var confirmDelete by remember { mutableStateOf(false) }
    var message by remember { mutableStateOf<String?>(null) }
    var overlay by remember(id) { mutableStateOf<Bitmap?>(null) }

    LaunchedEffect(id) { graph.uploads.refresh(id) }
    LaunchedEffect(record?.overlayUrl) {
        val r = record ?: return@LaunchedEffect
        val url = r.overlayUrl ?: return@LaunchedEffect
        overlay = withContext(Dispatchers.IO) {
            runCatching {
                val bytes = BackendClient(r.backendUrl, graph.settings.token(r.backendUrl)).bytes(url)
                BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
            }.getOrNull()
        }
    }
    if (record == null) {
        LaunchedEffect(Unit) { onBack() }
        return
    }
    val st = statusOf(record, progress[id])
    val canUpload = (record.status == UploadStatus.SAVED && !record.queued) || record.status == UploadStatus.FAILED

    fun share() = scope.launch {
        val zip = withContext(Dispatchers.IO) {
            runCatching { graph.store.archive(id).also { if (!it.exists()) CaptureWriter.archive(graph.store.folder(id), it) } }
        }.getOrElse { message = "Could not prepare the archive."; return@launch }
        val uri = FileProvider.getUriForFile(context, context.packageName + ".files", zip)
        context.startActivity(Intent.createChooser(Intent(Intent.ACTION_SEND).apply {
            type = "application/zip"; putExtra(Intent.EXTRA_STREAM, uri); addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }, null))
    }

    Column(Modifier.fillMaxSize().background(Ds.paper).windowInsetsPadding(WindowInsets.safeDrawing)) {
        Row(Modifier.fillMaxWidth().padding(horizontal = Ds.s4, vertical = Ds.s2), verticalAlignment = Alignment.CenterVertically) {
            RoundIconButton(Icons.AutoMirrored.Outlined.ArrowBack, "Back", onBack)
            Spacer(Modifier.width(Ds.s3))
            Text(formatDate(record.createdUnix), style = DsType.body.copy(fontWeight = androidx.compose.ui.text.font.FontWeight.Medium), color = Ds.ink, modifier = Modifier.weight(1f))
            RoundIconButton(Icons.Outlined.IosShare, "Share", { share() }, enabled = record.status != UploadStatus.UPLOADING)
            Spacer(Modifier.width(Ds.s2))
            RoundIconButton(Icons.Outlined.DeleteOutline, "Delete", { confirmDelete = true }, enabled = record.status != UploadStatus.UPLOADING)
        }

        Box(Modifier.weight(1f).fillMaxWidth().padding(horizontal = Ds.s4).clip(Ds.rMd).background(Ds.vp)) {
            val o = overlay
            if (o != null) Image(o.asImageBitmap(), "Findings", Modifier.fillMaxSize(), contentScale = ContentScale.Fit)
            else CapturePhoto(graph.store.photo(id), Modifier.fillMaxSize(), maxSide = 1600, contentScale = ContentScale.Fit)
        }

        Column(Modifier.fillMaxWidth().padding(Ds.s5), verticalArrangement = Arrangement.spacedBy(Ds.s3)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                StatusDot(st.tone)
                Spacer(Modifier.width(8.dp))
                Text(st.text, style = DsType.body, color = Ds.ink)
            }
            if (record.status == UploadStatus.UPLOADING && record.stage == UploadStage.TRANSFERRING) {
                LinearProgressIndicator(progress = { progress[id] ?: 0f }, modifier = Modifier.fillMaxWidth().height(3.dp).clip(Ds.rPill),
                    color = Ds.blue, trackColor = Ds.paper2, drawStopIndicator = {})
            }
            st.detail?.let { Text(it, style = DsType.bodyS, color = Ds.rust) }
            record.findings?.takeIf { it.isNotEmpty() }?.let { f ->
                FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    f.forEach { Chip(findingLabel(it), Tone.ATTENTION, glyph = "+") }
                }
            }
            if (canUpload) DsButton(if (record.status == UploadStatus.FAILED) "Retry upload" else "Upload capture",
                { graph.uploads.enqueue(id)?.let { message = it } }, Modifier.fillMaxWidth(), icon = Icons.Outlined.CloudUpload)
            else if (record.queued && record.status != UploadStatus.UPLOADED)
                DsButton("Cancel upload", { graph.uploads.cancel(id) }, Modifier.fillMaxWidth(), style = BtnStyle.SECONDARY)
        }
    }

    if (confirmDelete) AlertDialog(
        onDismissRequest = { confirmDelete = false },
        title = { Text("Delete capture?", style = DsType.displayS) },
        confirmButton = { TextButton({ confirmDelete = false; graph.uploads.cancel(id); graph.store.delete(id); onBack() }) { Text("Delete", color = Ds.rust, style = DsType.button) } },
        dismissButton = { TextButton({ confirmDelete = false }) { Text("Cancel", color = Ds.ink, style = DsType.button) } },
        containerColor = Ds.card, shape = Ds.rCard,
    )
    message?.let { m ->
        AlertDialog(
            onDismissRequest = { message = null }, text = { Text(m, style = DsType.body) },
            confirmButton = { TextButton({ message = null }) { Text("OK", color = Ds.blue, style = DsType.button) } },
            containerColor = Ds.card, shape = Ds.rCard,
        )
    }
}
