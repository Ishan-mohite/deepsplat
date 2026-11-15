package com.deepsplat.capture.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowForward
import androidx.compose.material.icons.outlined.FiberManualRecord
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.material.icons.outlined.ViewInAr
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
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
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.deepsplat.capture.AppGraph
import com.deepsplat.capture.core.normalizeBackendUrl
import com.deepsplat.capture.data.BackendClient
import com.deepsplat.capture.data.CaptureRecord
import com.deepsplat.capture.data.UploadStage
import com.deepsplat.capture.data.UploadStatus
import com.deepsplat.capture.ui.Bucket
import com.deepsplat.capture.ui.bucket
import com.deepsplat.capture.ui.components.BtnStyle
import com.deepsplat.capture.ui.components.Chip
import com.deepsplat.capture.ui.components.DsButton
import com.deepsplat.capture.ui.components.DsCard
import com.deepsplat.capture.ui.components.Eyebrow
import com.deepsplat.capture.ui.components.Kpi
import com.deepsplat.capture.ui.components.Reticle
import com.deepsplat.capture.ui.components.RoundIconButton
import com.deepsplat.capture.ui.components.Segmented
import com.deepsplat.capture.ui.components.Thumb
import com.deepsplat.capture.ui.components.Tone
import com.deepsplat.capture.ui.components.Wordmark
import com.deepsplat.capture.ui.formatDate
import com.deepsplat.capture.ui.formatSeconds
import com.deepsplat.capture.ui.statusOf
import com.deepsplat.capture.ui.theme.Ds
import com.deepsplat.capture.ui.theme.DsType
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

private enum class Link { CHECKING, ONLINE, OFFLINE, NOT_SET }

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun HomeScreen(graph: AppGraph, onRecord: () -> Unit, onOpen: (String) -> Unit, onSettings: () -> Unit) {
    val settings by graph.settings.state.collectAsState()
    val records by graph.store.records.collectAsState()
    val progress by graph.store.progress.collectAsState()
    var filter by rememberSaveable { mutableIntStateOf(0) }
    var refreshing by remember { mutableStateOf(false) }
    var tick by remember { mutableIntStateOf(0) }
    val scope = rememberCoroutineScope()

    var link by remember { mutableStateOf(Link.CHECKING) }
    LaunchedEffect(settings.backendUrl, tick) {
        val url = normalizeBackendUrl(settings.backendUrl)
        if (url == null) { link = Link.NOT_SET; return@LaunchedEffect }
        while (true) {
            link = withContext(Dispatchers.IO) { runCatching { BackendClient(url, "").health().ok }.getOrDefault(false) }
                .let { if (it) Link.ONLINE else Link.OFFLINE }
            delay(20_000)
        }
    }
    LaunchedEffect(Unit) { graph.store.recoverFromDisk() }

    val visible = records.filter {
        when (filter) { 1 -> bucket(it) == Bucket.WAITING; 2 -> bucket(it) == Bucket.UPLOADED; else -> true }
    }
    val placed = records.count { it.backendStatus == "done" && it.backendDetail == "done" }
    val findings = records.sumOf { it.findings?.size ?: 0 }

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
        modifier = Modifier.fillMaxSize().background(Ds.paper),
    ) {
        LazyColumn(
            Modifier.fillMaxSize().windowInsetsPadding(WindowInsets.safeDrawing),
            contentPadding = PaddingValues(start = Ds.s5, end = Ds.s5, top = Ds.s3, bottom = Ds.s10),
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            item {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Wordmark()
                    Spacer(Modifier.weight(1f))
                    RoundIconButton(Icons.Outlined.Settings, "Settings", onSettings)
                }
            }
            item {
                Column(Modifier.padding(top = Ds.s4, bottom = Ds.s2)) {
                    Eyebrow("Field inspections")
                    Spacer(Modifier.height(10.dp))
                    Text(settings.mapName.ifBlank { settings.mapId.ifBlank { "Your inspections" } }, style = DsType.displayL, color = Ds.ink,
                        maxLines = 2, overflow = TextOverflow.Ellipsis)
                    Spacer(Modifier.height(10.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                        when (link) {
                            Link.ONLINE -> Chip("Backend connected", Tone.LIVE)
                            Link.OFFLINE -> Chip("Backend offline · uploads wait", Tone.WARN, glyph = "!")
                            Link.CHECKING -> Chip("Checking connection", Tone.NEUTRAL, glyph = "◌")
                            Link.NOT_SET -> Chip("No backend set", Tone.WARN, glyph = "!")
                        }
                        if (settings.mapId.isNotBlank() && settings.mapName.isNotBlank()) Text(settings.mapId, style = DsType.monoS, color = Ds.muted)
                    }
                }
            }
            item { RecordHero(settings.mapId.isNotBlank(), onRecord, onSettings) }
            item {
                DsCard(padding = PaddingValues(horizontal = Ds.s5, vertical = Ds.s4)) {
                    Row(Modifier.fillMaxWidth()) {
                        Kpi("Captures", "${records.size}", Modifier.weight(1f))
                        Kpi("Uploaded", "${records.count { it.status == UploadStatus.UPLOADED }}", Modifier.weight(1f))
                        Kpi("Placed", "$placed", Modifier.weight(1f))
                        // Rust only for open findings.
                        Kpi("Findings", "$findings", Modifier.weight(1f), valueColor = if (findings > 0) Ds.rust else Ds.ink)
                    }
                }
            }
            item {
                Row(Modifier.padding(top = Ds.s4), verticalAlignment = Alignment.CenterVertically) {
                    Text("Recent captures", style = DsType.displayS, color = Ds.ink, modifier = Modifier.weight(1f))
                    Text("%02d".format(records.size), style = DsType.mono, color = Ds.muted)
                }
            }
            if (records.isNotEmpty()) item { Segmented(listOf("All", "Waiting", "Uploaded"), filter, { filter = it }) }
            if (visible.isEmpty()) item {
                DsCard {
                    Icon(Icons.Outlined.ViewInAr, null, tint = Ds.blue, modifier = Modifier.size(26.dp))
                    Spacer(Modifier.height(12.dp))
                    Text(if (records.isEmpty()) "Your first capture starts here." else "No captures in this view.", style = DsType.body.copy(fontWeight = androidx.compose.ui.text.font.FontWeight.Medium), color = Ds.ink)
                    Spacer(Modifier.height(4.dp))
                    Text(
                        if (records.isEmpty()) "Sweep slowly across the room towards the defect, then take a sharp photo of it. The backend places it in the 3D scan."
                        else "Try another filter to see your saved inspections.",
                        style = DsType.bodyS, color = Ds.ink2,
                    )
                }
            }
            items(visible, key = { it.id }) { r -> CaptureRow(graph, r, progress[r.id]) { onOpen(r.id) } }
        }
    }
}

@Composable
private fun RecordHero(ready: Boolean, onRecord: () -> Unit, onSettings: () -> Unit) {
    Box(
        Modifier.fillMaxWidth().clip(Ds.rCard)
            .background(Brush.verticalGradient(listOf(Ds.vp2, Ds.vp)))
            .border(1.dp, Ds.vpLine, Ds.rCard),
    ) {
        Reticle(Modifier.align(Alignment.TopEnd).padding(top = 6.dp, end = 6.dp).size(132.dp), active = ready)
        Column(Modifier.padding(Ds.s6)) {
            Eyebrow("New capture", color = Ds.pick)
            Spacer(Modifier.height(10.dp))
            Text("Record a defect", style = DsType.displayM, color = Ds.vpInk)
            Spacer(Modifier.height(6.dp))
            Text(
                if (ready) "Sweep the room, then photograph the defect.\nAbout 20 seconds."
                else "Choose the site you are inspecting first.",
                style = DsType.bodyS, color = Ds.vpMuted, modifier = Modifier.width(220.dp),
            )
            Spacer(Modifier.height(Ds.s5))
            if (ready) DsButton("Record defect", onRecord, Modifier.fillMaxWidth(), style = BtnStyle.ON_DARK_PRIMARY,
                icon = Icons.Outlined.FiberManualRecord, trailing = Icons.AutoMirrored.Outlined.ArrowForward)
            else DsButton("Choose site", onSettings, Modifier.fillMaxWidth(), style = BtnStyle.ON_DARK_PRIMARY,
                trailing = Icons.AutoMirrored.Outlined.ArrowForward)
        }
    }
}

@Composable
private fun CaptureRow(graph: AppGraph, r: CaptureRecord, progress: Float?, onClick: () -> Unit) {
    val st = statusOf(r, progress)
    DsCard(padding = PaddingValues(12.dp), onClick = onClick) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Thumb(graph.store.photo(r.id))
            Spacer(Modifier.width(14.dp))
            Column(Modifier.weight(1f)) {
                Text("Capture · ${r.mapName ?: r.mapId}", style = DsType.body.copy(fontWeight = androidx.compose.ui.text.font.FontWeight.SemiBold),
                    color = Ds.ink, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Spacer(Modifier.height(2.dp))
                Text("${formatDate(r.createdUnix)} · ${r.frameCount} frames · ${formatSeconds(r.durationS)}", style = DsType.monoS, color = Ds.ink2, maxLines = 1)
                Spacer(Modifier.height(8.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    Chip(st.text, st.tone, glyph = st.glyph)
                    val n = r.findings?.size ?: 0
                    if (n > 0) Chip(if (n == 1) "1 finding" else "$n findings", Tone.ATTENTION, glyph = "+")
                }
            }
        }
        if (r.status == UploadStatus.UPLOADING && r.stage == UploadStage.TRANSFERRING) {
            Spacer(Modifier.height(10.dp))
            LinearProgressIndicator(
                progress = { progress ?: 0f }, modifier = Modifier.fillMaxWidth().height(3.dp).clip(Ds.rPill),
                color = Ds.blue, trackColor = Ds.paper2, drawStopIndicator = {},
            )
        }
    }
}
