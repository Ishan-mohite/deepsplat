package com.deepsplat.capture.ui.screens

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.CloudUpload
import androidx.compose.material.icons.outlined.DeleteOutline
import androidx.compose.material.icons.outlined.ExpandLess
import androidx.compose.material.icons.outlined.ExpandMore
import androidx.compose.material.icons.outlined.IosShare
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.core.content.FileProvider
import com.deepsplat.capture.AppGraph
import com.deepsplat.capture.core.CaptureManifest
import com.deepsplat.capture.core.CaptureWriter
import com.deepsplat.capture.data.BackendClient
import com.deepsplat.capture.data.UploadStage
import com.deepsplat.capture.data.UploadStatus
import com.deepsplat.capture.ui.components.BtnStyle
import com.deepsplat.capture.ui.components.CapturePhoto
import com.deepsplat.capture.ui.components.Chip
import com.deepsplat.capture.ui.components.DetailRow
import com.deepsplat.capture.ui.components.Divider
import com.deepsplat.capture.ui.components.DsButton
import com.deepsplat.capture.ui.components.DsCard
import com.deepsplat.capture.ui.components.Eyebrow
import com.deepsplat.capture.ui.components.Kpi
import com.deepsplat.capture.ui.components.RoundIconButton
import com.deepsplat.capture.ui.components.Segmented
import com.deepsplat.capture.ui.components.Tone
import com.deepsplat.capture.ui.findingLabel
import com.deepsplat.capture.ui.formatDate
import com.deepsplat.capture.ui.formatSeconds
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
    var sharing by remember { mutableStateOf(false) }
    var message by remember { mutableStateOf<String?>(null) }
    var showDetails by remember { mutableStateOf(false) }
    var view by remember { mutableIntStateOf(0) }

    var manifest by remember(id) { mutableStateOf<CaptureManifest?>(null) }
    LaunchedEffect(id) {
        manifest = withContext(Dispatchers.IO) { runCatching { CaptureWriter.readManifest(graph.store.folder(id)) }.getOrNull() }
    }
    var overlay by remember(id) { mutableStateOf<Bitmap?>(null) }
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
    LaunchedEffect(id) { graph.uploads.refresh(id) }

    if (record == null) {
        LaunchedEffect(Unit) { onBack() }
        return
    }
    val st = statusOf(record, progress[id])
    val canUpload = record.status == UploadStatus.SAVED && !record.queued || record.status == UploadStatus.FAILED

    Column(Modifier.fillMaxSize().background(Ds.paper).windowInsetsPadding(WindowInsets.safeDrawing)) {
        Row(Modifier.fillMaxWidth().padding(horizontal = Ds.s5, vertical = Ds.s3), verticalAlignment = Alignment.CenterVertically) {
            RoundIconButton(Icons.AutoMirrored.Outlined.ArrowBack, "Back", onBack)
            Spacer(Modifier.weight(1f))
            RoundIconButton(Icons.Outlined.DeleteOutline, "Delete capture", { confirmDelete = true }, enabled = record.status != UploadStatus.UPLOADING)
        }
        Column(Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(horizontal = Ds.s5), verticalArrangement = Arrangement.spacedBy(14.dp)) {
            Column(Modifier.padding(top = Ds.s2, bottom = Ds.s2)) {
                Eyebrow(if (record.status == UploadStatus.UPLOADED) "Capture delivered" else "Capture saved")
                Spacer(Modifier.height(10.dp))
                Text(record.mapName ?: record.mapId, style = DsType.displayL, color = Ds.ink)
                Spacer(Modifier.height(6.dp))
                Text(formatDate(record.createdUnix), style = DsType.mono, color = Ds.ink2)
            }

            // Photo, or the backend's detection overlay once it exists.
            Box(Modifier.fillMaxWidth().aspectRatio(3f / 4f).clip(Ds.rMd).background(Ds.vp).border(1.dp, Ds.vpLine, Ds.rMd)) {
                val o = overlay
                if (view == 1 && o != null) Image(o.asImageBitmap(), "Findings", Modifier.fillMaxSize(), contentScale = ContentScale.Fit)
                else CapturePhoto(graph.store.photo(id), Modifier.fillMaxSize(), maxSide = 1600, contentScale = ContentScale.Fit)
                Row(Modifier.align(Alignment.BottomStart).padding(14.dp)) {
                    Chip(if (view == 1 && o != null) "Findings" else "Defect photo", Tone.NEUTRAL, onDark = true, glyph = "◉")
                }
            }
            if (overlay != null) Segmented(listOf("Photo", "Findings"), view, { view = it })

            DsCard(padding = PaddingValues(horizontal = Ds.s5, vertical = Ds.s4)) {
                Row(Modifier.fillMaxWidth()) {
                    Kpi("Frames", "${record.frameCount}", Modifier.weight(1f))
                    Kpi("Duration", formatSeconds(record.durationS), Modifier.weight(1f))
                    Kpi("Motion", "${manifest?.imu?.rateHz?.toInt() ?: 100} Hz", Modifier.weight(1f))
                }
            }

            DsCard {
                Eyebrow(if (record.status == UploadStatus.UPLOADED) "Backend" else "Delivery")
                Spacer(Modifier.height(12.dp))
                Chip(st.text, st.tone, glyph = st.glyph)
                st.detail?.let {
                    Spacer(Modifier.height(10.dp))
                    Text(it, style = DsType.bodyS, color = if (st.tone == Tone.ATTENTION) Ds.rust else Ds.ink2)
                }
                if (record.status == UploadStatus.UPLOADING && record.stage == UploadStage.TRANSFERRING) {
                    Spacer(Modifier.height(12.dp))
                    LinearProgressIndicator(progress = { progress[id] ?: 0f }, modifier = Modifier.fillMaxWidth().height(4.dp).clip(Ds.rPill),
                        color = Ds.blue, trackColor = Ds.paper2, drawStopIndicator = {})
                }
                val f = record.findings
                if (!f.isNullOrEmpty()) {
                    Spacer(Modifier.height(Ds.s4))
                    Divider()
                    Spacer(Modifier.height(Ds.s4))
                    Text(if (f.size == 1) "1 finding" else "${f.size} findings", style = DsType.displayS, color = Ds.ink)
                    Spacer(Modifier.height(10.dp))
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        f.forEach { Chip(findingLabel(it), Tone.ATTENTION, glyph = "+") }
                    }
                } else if (record.processingFinished && record.backendStatus == "done") {
                    Spacer(Modifier.height(10.dp))
                    Text("No damage found in the photo.", style = DsType.bodyS, color = Ds.ink2)
                }
            }

            DsCard {
                Row(Modifier.fillMaxWidth().clickable { showDetails = !showDetails }, verticalAlignment = Alignment.CenterVertically) {
                    Text("Capture details", style = DsType.body.copy(fontWeight = FontWeight.Medium), color = Ds.ink, modifier = Modifier.weight(1f))
                    Icon(if (showDetails) Icons.Outlined.ExpandLess else Icons.Outlined.ExpandMore, null, tint = Ds.ink2)
                }
                if (showDetails) {
                    Spacer(Modifier.height(10.dp))
                    DetailRow("Capture ID", record.id.take(8), mono = true)
                    manifest?.let { m ->
                        DetailRow("Image size", "${m.photo.intrinsics.width} × ${m.photo.intrinsics.height}", mono = true)
                        DetailRow("Frame sampling", "${m.video.fpsTarget.toInt()} fps")
                        DetailRow("Accelerometer", "${m.imu.sampleCounts["accel"] ?: 0} samples", mono = true)
                        DetailRow("Gyroscope", "${m.imu.sampleCounts["gyro"] ?: 0} samples", mono = true)
                        DetailRow("Device motion", "${m.imu.sampleCounts["motion"] ?: 0} samples", mono = true)
                        DetailRow("GPS hint", if (m.geo == null) "Not recorded" else "Included")
                        DetailRow("Device", m.device)
                    }
                    Spacer(Modifier.height(6.dp))
                    DsButton("Copy capture ID", {
                        context.getSystemService(ClipboardManager::class.java).setPrimaryClip(ClipData.newPlainText("Capture ID", record.id))
                    }, style = BtnStyle.GHOST, small = true)
                }
            }
            Text("Saved on this phone until you delete it.", style = DsType.bodyS, color = Ds.muted, modifier = Modifier.padding(bottom = Ds.s4))
        }

        Column(
            Modifier.fillMaxWidth().background(Ds.paper).navigationBarsPadding().padding(horizontal = Ds.s5, vertical = Ds.s3),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Divider(Modifier.padding(bottom = 6.dp))
            if (canUpload) DsButton(if (record.status == UploadStatus.FAILED) "Retry upload" else "Upload capture",
                { graph.uploads.enqueue(id)?.let { message = it } }, Modifier.fillMaxWidth(), icon = Icons.Outlined.CloudUpload)
            else if (record.queued && record.status != UploadStatus.UPLOADED) DsButton("Cancel upload", { graph.uploads.cancel(id) }, Modifier.fillMaxWidth(), style = BtnStyle.SECONDARY)
            DsButton("Share archive", {
                sharing = true
                scope.launch {
                    val result = withContext(Dispatchers.IO) {
                        runCatching {
                            val zip = graph.store.archive(id)
                            if (!zip.exists()) CaptureWriter.archive(graph.store.folder(id), zip)
                            zip
                        }
                    }
                    sharing = false
                    result.onSuccess { zip ->
                        val uri = FileProvider.getUriForFile(context, context.packageName + ".files", zip)
                        context.startActivity(Intent.createChooser(Intent(Intent.ACTION_SEND).apply {
                            type = "application/zip"; putExtra(Intent.EXTRA_STREAM, uri); addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                        }, "Share capture"))
                    }.onFailure { message = "The archive could not be prepared: ${it.message}" }
                }
            }, Modifier.fillMaxWidth(), style = BtnStyle.SECONDARY, icon = Icons.Outlined.IosShare, loading = sharing,
                enabled = record.status != UploadStatus.UPLOADING)
        }
    }

    if (confirmDelete) AlertDialog(
        onDismissRequest = { confirmDelete = false },
        title = { Text("Delete this capture?", style = DsType.displayS) },
        text = { Text(if (record.status == UploadStatus.UPLOADED) "The copy on this phone is deleted. The backend keeps its copy." else "The capture has not been uploaded. It is deleted for good.", style = DsType.body) },
        confirmButton = { TextButton({ confirmDelete = false; graph.uploads.cancel(id); graph.store.delete(id); onBack() }) { Text("Delete capture", color = Ds.rust, style = DsType.button) } },
        dismissButton = { TextButton({ confirmDelete = false }) { Text("Cancel", color = Ds.ink, style = DsType.button) } },
        containerColor = Ds.card, shape = Ds.rCard,
    )
    message?.let { m ->
        AlertDialog(
            onDismissRequest = { message = null }, title = { Text("Capture", style = DsType.displayS) }, text = { Text(m, style = DsType.body) },
            confirmButton = { TextButton({ message = null }) { Text("OK", color = Ds.blue, style = DsType.button) } },
            containerColor = Ds.card, shape = Ds.rCard,
        )
    }
}

