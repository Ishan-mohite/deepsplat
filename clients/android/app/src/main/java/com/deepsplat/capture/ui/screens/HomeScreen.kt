package com.deepsplat.capture.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.FiberManualRecord
import androidx.compose.material.icons.outlined.FileDownload
import androidx.compose.material.icons.outlined.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.TextButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.deepsplat.capture.AppGraph
import com.deepsplat.capture.core.normalizeBackendUrl
import com.deepsplat.capture.data.BackendClient
import com.deepsplat.capture.data.CaptureRecord
import com.deepsplat.capture.data.UploadStage
import com.deepsplat.capture.data.UploadStatus
import com.deepsplat.capture.ui.components.Chip
import com.deepsplat.capture.ui.components.Divider
import com.deepsplat.capture.ui.components.DsButton
import com.deepsplat.capture.ui.components.RoundIconButton
import com.deepsplat.capture.ui.components.Thumb
import com.deepsplat.capture.ui.components.Tone
import com.deepsplat.capture.ui.components.Wordmark
import com.deepsplat.capture.ui.formatDate
import com.deepsplat.capture.ui.statusOf
import com.deepsplat.capture.ui.theme.Ds
import com.deepsplat.capture.ui.theme.DsType
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun HomeScreen(graph: AppGraph, onRecord: () -> Unit, onOpen: (String) -> Unit, onSettings: () -> Unit) {
    val settings by graph.settings.state.collectAsState()
    val records by graph.store.records.collectAsState()
    val progress by graph.store.progress.collectAsState()
    var refreshing by remember { mutableStateOf(false) }
    var tick by remember { mutableIntStateOf(0) }
    var online by remember { mutableStateOf<Boolean?>(null) }
    val scope = rememberCoroutineScope()
    val configured = normalizeBackendUrl(settings.backendUrl) != null && settings.mapId.isNotBlank()
    val importing by graph.importProgress.collectAsState()
    val importError by graph.importError.collectAsState()
    var importMenu by remember { mutableStateOf(false) }
    val pickZip = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri -> uri?.let { graph.import(it, zip = true) } }
    val pickVideo = rememberLauncherForActivityResult(ActivityResultContracts.PickVisualMedia()) { uri -> uri?.let { graph.import(it, zip = false) } }

    LaunchedEffect(settings.backendUrl, tick) {
        val url = normalizeBackendUrl(settings.backendUrl) ?: run { online = null; return@LaunchedEffect }
        while (true) {
            online = withContext(Dispatchers.IO) { runCatching { BackendClient(url, "").health().ok }.getOrDefault(false) }
            delay(20_000)
        }
    }
    LaunchedEffect(Unit) { graph.store.recoverFromDisk() }

    Column(Modifier.fillMaxSize().background(Ds.paper).windowInsetsPadding(WindowInsets.safeDrawing)) {
        Row(Modifier.fillMaxWidth().padding(start = Ds.s5, end = Ds.s4, top = Ds.s2), verticalAlignment = Alignment.CenterVertically) {
            Wordmark()
            Spacer(Modifier.weight(1f))
            Box {
                RoundIconButton(Icons.Outlined.FileDownload, "Import", { importMenu = true }, enabled = importing == null && settings.mapId.isNotBlank())
                DropdownMenu(importMenu, { importMenu = false }, containerColor = Ds.card, shape = Ds.rMd) {
                    DropdownMenuItem({ Text("Capture zip", style = DsType.body) }, {
                        importMenu = false; pickZip.launch(arrayOf("application/zip", "application/x-zip-compressed", "application/octet-stream"))
                    })
                    DropdownMenuItem({ Text("Video", style = DsType.body) }, {
                        importMenu = false; pickVideo.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.VideoOnly))
                    })
                }
            }
            Spacer(Modifier.width(Ds.s2))
            RoundIconButton(Icons.Outlined.Settings, "Settings", onSettings)
        }
        Row(Modifier.fillMaxWidth().padding(horizontal = Ds.s5, vertical = Ds.s4), verticalAlignment = Alignment.CenterVertically) {
            Text(settings.mapName.ifBlank { settings.mapId.ifBlank { "No site" } }, style = DsType.displayM, color = Ds.ink,
                maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
            when (online) {
                true -> Chip("Online", Tone.LIVE)
                false -> Chip("Offline", Tone.WARN, glyph = "!")
                null -> Unit
            }
        }

        importing?.let { p ->
            Column(Modifier.fillMaxWidth().padding(horizontal = Ds.s5).padding(bottom = Ds.s2)) {
                Text("Importing", style = DsType.bodyS, color = Ds.ink2)
                Spacer(Modifier.height(6.dp))
                LinearProgressIndicator(progress = { p }, modifier = Modifier.fillMaxWidth().height(2.dp).clip(Ds.rPill),
                    color = Ds.blue, trackColor = Ds.paper2, drawStopIndicator = {})
            }
        }

        PullToRefreshBox(
            isRefreshing = refreshing,
            onRefresh = {
                scope.launch {
                    refreshing = true
                    records.filter { it.status == UploadStatus.UPLOADED }.forEach { graph.uploads.refresh(it.id) }
                    tick++
                    refreshing = false
                }
            },
            modifier = Modifier.weight(1f).fillMaxWidth(),
        ) {
            LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(horizontal = Ds.s5, vertical = Ds.s1)) {
                if (records.isEmpty()) item { Text("No captures yet", style = DsType.bodyS, color = Ds.muted) }
                itemsIndexed(records, key = { _, r -> r.id }) { i, r ->
                    if (i > 0) Divider()
                    CaptureRow(graph, r, progress[r.id]) { onOpen(r.id) }
                }
            }
        }

        Box(Modifier.fillMaxWidth().navigationBarsPadding().padding(Ds.s5)) {
            if (configured) DsButton("Record defect", onRecord, Modifier.fillMaxWidth(), icon = Icons.Outlined.FiberManualRecord)
            else DsButton("Set up", onSettings, Modifier.fillMaxWidth())
        }
    }
    importError?.let { ImportErrorDialog(it) { graph.importError.value = null } }
}

@Composable
private fun ImportErrorDialog(message: String, onDismiss: () -> Unit) = AlertDialog(
    onDismissRequest = onDismiss, text = { Text(message, style = DsType.body) },
    confirmButton = { TextButton(onDismiss) { Text("OK", color = Ds.blue, style = DsType.button) } },
    containerColor = Ds.card, shape = Ds.rCard,
)

@Composable
private fun CaptureRow(graph: AppGraph, r: CaptureRecord, progress: Float?, onClick: () -> Unit) {
    val st = statusOf(r, progress)
    Row(Modifier.fillMaxWidth().clickable(onClick = onClick).padding(vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
        Thumb(graph.store.photo(r.id), Modifier.size(48.dp))
        Spacer(Modifier.width(Ds.s3))
        Column(Modifier.weight(1f)) {
            Text(formatDate(r.createdUnix), style = DsType.body.copy(fontWeight = FontWeight.Medium), color = Ds.ink, maxLines = 1)
            Spacer(Modifier.height(2.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                StatusDot(st.tone)
                Spacer(Modifier.width(6.dp))
                Text(st.text, style = DsType.bodyS, color = Ds.ink2, maxLines = 1)
                val n = r.findings?.size ?: 0
                if (n > 0) Text(" · $n ${if (n == 1) "finding" else "findings"}", style = DsType.bodyS.copy(fontWeight = FontWeight.SemiBold), color = Ds.rust)
            }
            if (r.status == UploadStatus.UPLOADING && r.stage == UploadStage.TRANSFERRING) {
                Spacer(Modifier.height(6.dp))
                LinearProgressIndicator(progress = { progress ?: 0f }, modifier = Modifier.fillMaxWidth().height(2.dp).clip(Ds.rPill),
                    color = Ds.blue, trackColor = Ds.paper2, drawStopIndicator = {})
            }
        }
    }
}

/** Status dot; always next to its label. */
@Composable
fun StatusDot(tone: Tone) {
    val c = when (tone) {
        Tone.LIVE -> Ds.green
        Tone.WARN -> Ds.amber
        Tone.ATTENTION -> Ds.rust
        Tone.INFO, Tone.SELECTED -> Ds.blue
        Tone.NEUTRAL -> Ds.muted
    }
    Box(Modifier.size(7.dp).clip(CircleShape).background(c))
}
