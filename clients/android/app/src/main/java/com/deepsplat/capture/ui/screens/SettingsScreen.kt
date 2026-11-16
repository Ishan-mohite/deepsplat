package com.deepsplat.capture.ui.screens

import android.Manifest
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.Check
import androidx.compose.material.icons.outlined.Visibility
import androidx.compose.material.icons.outlined.VisibilityOff
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Text
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import com.deepsplat.capture.AppGraph
import com.deepsplat.capture.core.normalizeBackendUrl
import com.deepsplat.capture.data.BackendClient
import com.deepsplat.capture.data.MapSummary
import com.deepsplat.capture.ui.components.BtnStyle
import com.deepsplat.capture.ui.components.Divider
import com.deepsplat.capture.ui.components.DsButton
import com.deepsplat.capture.ui.components.DsCard
import com.deepsplat.capture.ui.components.DsTextField
import com.deepsplat.capture.ui.components.RoundIconButton
import com.deepsplat.capture.ui.components.SettingSwitch
import com.deepsplat.capture.ui.components.Tone
import com.deepsplat.capture.ui.theme.Ds
import com.deepsplat.capture.ui.theme.DsType
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

private enum class Conn { IDLE, RUNNING, OK, BAD_TOKEN, UNREACHABLE }

@Composable
fun SettingsScreen(graph: AppGraph, onBack: () -> Unit) {
    val settings by graph.settings.state.collectAsState()
    val scope = rememberCoroutineScope()
    var url by remember { mutableStateOf(settings.backendUrl) }
    var token by remember { mutableStateOf(graph.settings.token()) }
    var showToken by remember { mutableStateOf(false) }
    var conn by remember { mutableStateOf(Conn.IDLE) }
    var maps by remember { mutableStateOf<List<MapSummary>?>(null) }
    var recorder by remember { mutableStateOf(settings.recorder) }
    val normalized = normalizeBackendUrl(url)
    val locationPermission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { ok ->
        graph.settings.update { it.copy(recordGps = ok) }
    }

    fun save() {
        val n = normalizeBackendUrl(url) ?: return
        graph.settings.update { it.copy(backendUrl = n, recorder = recorder.trim()) }
        graph.settings.setToken(token, n)
    }

    fun connect() {
        val n = normalizeBackendUrl(url) ?: return
        save()
        conn = Conn.RUNNING
        scope.launch {
            val c = BackendClient(n, token)
            conn = withContext(Dispatchers.IO) {
                when {
                    runCatching { c.health() }.isFailure -> Conn.UNREACHABLE
                    runCatching { c.tokenAccepted() }.getOrDefault(true) -> Conn.OK
                    else -> Conn.BAD_TOKEN
                }
            }
            maps = withContext(Dispatchers.IO) { runCatching { c.maps() }.getOrNull() }
            if (conn == Conn.OK) graph.uploads.retryNow()
        }
    }

    LaunchedEffect(Unit) {
        normalizeBackendUrl(settings.backendUrl)?.let { n ->
            maps = withContext(Dispatchers.IO) { runCatching { BackendClient(n, token).maps() }.getOrNull() }
        }
    }

    Column(Modifier.fillMaxSize().background(Ds.paper).windowInsetsPadding(WindowInsets.safeDrawing).imePadding()) {
        Row(Modifier.fillMaxWidth().padding(horizontal = Ds.s4, vertical = Ds.s2), verticalAlignment = Alignment.CenterVertically) {
            RoundIconButton(Icons.AutoMirrored.Outlined.ArrowBack, "Back", { save(); onBack() })
            Spacer(Modifier.width(Ds.s3))
            Text("Settings", style = DsType.displayS, color = Ds.ink)
        }
        Column(Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(horizontal = Ds.s4, vertical = Ds.s2), verticalArrangement = Arrangement.spacedBy(Ds.s2)) {
            Section("Backend") {
                DsTextField(url, { url = it; conn = Conn.IDLE }, "Address, e.g. 192.168.1.20:8000", mono = true,
                    keyboard = KeyboardOptions(keyboardType = KeyboardType.Uri, imeAction = ImeAction.Next, autoCorrectEnabled = false))
                Spacer(Modifier.height(Ds.s2))
                DsTextField(token, { token = it; conn = Conn.IDLE }, "Token", secret = !showToken, mono = true,
                    keyboard = KeyboardOptions(keyboardType = KeyboardType.Password, imeAction = ImeAction.Done, autoCorrectEnabled = false)) {
                    IconButton({ showToken = !showToken }) {
                        Icon(if (showToken) Icons.Outlined.VisibilityOff else Icons.Outlined.Visibility, if (showToken) "Hide token" else "Show token", tint = Ds.ink2)
                    }
                }
                Spacer(Modifier.height(Ds.s3))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    DsButton("Connect", ::connect, style = BtnStyle.DARK, small = true, enabled = normalized != null, loading = conn == Conn.RUNNING)
                    Spacer(Modifier.width(Ds.s3))
                    val (text, tone) = when {
                        url.isNotBlank() && normalized == null -> "Use https or a local address" to Tone.ATTENTION
                        conn == Conn.OK -> "Connected" to Tone.LIVE
                        conn == Conn.BAD_TOKEN -> "Token rejected" to Tone.ATTENTION
                        conn == Conn.UNREACHABLE -> "Not reachable" to Tone.ATTENTION
                        else -> null to Tone.NEUTRAL
                    }
                    if (text != null) {
                        StatusDot(tone)
                        Spacer(Modifier.width(6.dp))
                        Text(text, style = DsType.bodyS, color = Ds.ink2)
                    }
                }
            }

            Section("Site") {
                val list = maps
                if (list.isNullOrEmpty()) Text(if (list == null) "Connect to load sites" else "No sites on this backend", style = DsType.bodyS, color = Ds.muted)
                else list.forEachIndexed { i, m ->
                    if (i > 0) Divider()
                    SiteRow(m, m.id == settings.mapId) { graph.settings.update { it.copy(mapId = m.id, mapName = m.name) } }
                }
            }

            Section("Recording") {
                OptionRow("Frame rate", listOf(3.0, 5.0, 10.0), settings.fps, { "${it.toInt()} fps" }) { v -> graph.settings.update { it.copy(fps = v) } }
                OptionRow("Max length", listOf(10.0, 20.0, 30.0, 60.0), settings.maxDuration, { "${it.toInt()} s" }) { v -> graph.settings.update { it.copy(maxDuration = v) } }
                SettingSwitch("Show tracked points", null, settings.showPoints) { v -> graph.settings.update { it.copy(showPoints = v) } }
                SettingSwitch("GPS hint", null, settings.recordGps) { v ->
                    if (v) locationPermission.launch(Manifest.permission.ACCESS_FINE_LOCATION) else graph.settings.update { it.copy(recordGps = false) }
                }
                Spacer(Modifier.height(Ds.s2))
                DsTextField(recorder, { recorder = it; graph.settings.update { s -> s.copy(recorder = it.trim()) } }, "Your name",
                    keyboard = KeyboardOptions(imeAction = ImeAction.Done))
            }

            Section("Uploads") {
                SettingSwitch("Upload automatically", null, settings.autoUpload) { v -> graph.settings.update { it.copy(autoUpload = v) } }
                SettingSwitch("Wi-Fi only", null, settings.wifiOnly) { v -> graph.settings.update { it.copy(wifiOnly = v) } }
            }
            Spacer(Modifier.height(Ds.s4))
        }
    }
}

@Composable
private fun Section(title: String, content: @Composable ColumnScope.() -> Unit) {
    Text(title, style = DsType.label, color = Ds.ink2, modifier = Modifier.padding(start = Ds.s2, top = Ds.s3))
    DsCard(Modifier.fillMaxWidth(), padding = PaddingValues(horizontal = Ds.s4, vertical = Ds.s3), content = content)
}

@Composable
private fun SiteRow(m: MapSummary, selected: Boolean, onSelect: () -> Unit) {
    Row(Modifier.fillMaxWidth().clickable(role = Role.RadioButton, onClick = onSelect).padding(vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(m.name, style = DsType.body.copy(fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal), color = Ds.ink, modifier = Modifier.weight(1f))
        if (m.vpsStatus != "ready") {
            Text(if (m.status != "ready" || m.vpsStatus == "building") "Preparing" else "No placement", style = DsType.bodyS, color = Ds.amber)
            Spacer(Modifier.width(Ds.s3))
        }
        Box(Modifier.size(22.dp), contentAlignment = Alignment.Center) {
            if (selected) Icon(Icons.Outlined.Check, "Selected", tint = Ds.blue)
        }
    }
}

@Composable
private fun OptionRow(title: String, values: List<Double>, current: Double, label: (Double) -> String, onPick: (Double) -> Unit) {
    Row(Modifier.fillMaxWidth().padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(title, style = DsType.body, color = Ds.ink, modifier = Modifier.weight(1f))
        Row(Modifier.clip(Ds.rPill).background(Ds.paper2).padding(3.dp)) {
            values.forEach { v ->
                val on = kotlin.math.abs(v - current) < 1e-6
                Box(
                    Modifier.clip(Ds.rPill).background(if (on) Ds.ink else Color.Transparent)
                        .clickable(role = Role.RadioButton) { onPick(v) }.padding(horizontal = 10.dp, vertical = 6.dp),
                ) { Text(label(v), style = DsType.label, color = if (on) Color.White else Ds.ink2) }
            }
        }
    }
}

