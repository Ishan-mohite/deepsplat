package com.deepsplat.capture.ui.screens

import android.Manifest
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
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
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material.icons.outlined.Visibility
import androidx.compose.material.icons.outlined.VisibilityOff
import androidx.compose.material3.CircularProgressIndicator
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
import com.deepsplat.capture.BuildConfig
import com.deepsplat.capture.core.normalizeBackendUrl
import com.deepsplat.capture.data.BackendClient
import com.deepsplat.capture.data.Health
import com.deepsplat.capture.data.MapSummary
import com.deepsplat.capture.ui.components.BtnStyle
import com.deepsplat.capture.ui.components.Chip
import com.deepsplat.capture.ui.components.DetailRow
import com.deepsplat.capture.ui.components.Divider
import com.deepsplat.capture.ui.components.DsButton
import com.deepsplat.capture.ui.components.DsCard
import com.deepsplat.capture.ui.components.DsTextField
import com.deepsplat.capture.ui.components.Eyebrow
import com.deepsplat.capture.ui.components.RoundIconButton
import com.deepsplat.capture.ui.components.SettingSwitch
import com.deepsplat.capture.ui.components.Tone
import com.deepsplat.capture.ui.theme.Ds
import com.deepsplat.capture.ui.theme.DsType
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

private sealed interface Check {
    data object Idle : Check
    data object Running : Check
    data class Done(val health: Health?, val tokenOk: Boolean?, val error: String?) : Check
}

@Composable
fun SettingsScreen(graph: AppGraph, onBack: () -> Unit) {
    val settings by graph.settings.state.collectAsState()
    val scope = rememberCoroutineScope()
    var url by remember { mutableStateOf(settings.backendUrl) }
    var token by remember { mutableStateOf(graph.settings.token()) }
    var showToken by remember { mutableStateOf(false) }
    var check by remember { mutableStateOf<Check>(Check.Idle) }
    var maps by remember { mutableStateOf<List<MapSummary>?>(null) }
    var mapsError by remember { mutableStateOf<String?>(null) }
    var loadingMaps by remember { mutableStateOf(false) }
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

    fun loadMaps() {
        val n = normalizeBackendUrl(url) ?: return
        loadingMaps = true; mapsError = null
        scope.launch {
            val r = withContext(Dispatchers.IO) { runCatching { BackendClient(n, token).maps() } }
            loadingMaps = false
            r.onSuccess { maps = it }.onFailure { mapsError = "The sites could not be loaded. Check the address and that the backend is running." }
        }
    }

    fun test() {
        val n = normalizeBackendUrl(url) ?: return
        save()
        check = Check.Running
        scope.launch {
            check = withContext(Dispatchers.IO) {
                val c = BackendClient(n, token)
                val h = runCatching { c.health() }
                if (h.isFailure) Check.Done(null, null, "No answer from $n. Phone and backend must be on the same network, and the backend must listen on its network address.")
                else Check.Done(h.getOrNull(), runCatching { c.tokenAccepted() }.getOrNull(), null)
            }
            loadMaps()
        }
    }

    LaunchedEffect(Unit) { if (normalizeBackendUrl(settings.backendUrl) != null) loadMaps() }

    Column(Modifier.fillMaxSize().background(Ds.paper).windowInsetsPadding(WindowInsets.safeDrawing).imePadding()) {
        Row(Modifier.fillMaxWidth().padding(horizontal = Ds.s5, vertical = Ds.s3), verticalAlignment = Alignment.CenterVertically) {
            RoundIconButton(Icons.AutoMirrored.Outlined.ArrowBack, "Back", { save(); onBack() })
            Spacer(Modifier.width(14.dp))
            Text("Settings", style = DsType.displayS, color = Ds.ink)
        }
        Column(Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(horizontal = Ds.s5), verticalArrangement = Arrangement.spacedBy(14.dp)) {
            // ---------------------------------------------------------------- backend
            DsCard {
                Eyebrow("Backend")
                Spacer(Modifier.height(10.dp))
                Text("Connect to your backend", style = DsType.displayS, color = Ds.ink)
                Spacer(Modifier.height(4.dp))
                Text("The address and token that scripts/start-backend.sh prints.", style = DsType.bodyS, color = Ds.ink2)
                Spacer(Modifier.height(Ds.s4))
                Text("Address", style = DsType.label, color = Ds.ink2)
                Spacer(Modifier.height(6.dp))
                DsTextField(url, { url = it; check = Check.Idle }, "192.168.1.20:8000", mono = true,
                    keyboard = KeyboardOptions(keyboardType = KeyboardType.Uri, imeAction = ImeAction.Next, autoCorrectEnabled = false))
                if (url.isNotBlank() && normalized == null) {
                    Spacer(Modifier.height(6.dp))
                    Text("Use https, or http only for addresses on your own network (192.168.x, 10.x, 172.16–31.x).", style = DsType.bodyS, color = Ds.rust)
                } else if (normalized != null && normalized != url.trim()) {
                    Spacer(Modifier.height(6.dp))
                    Text(normalized, style = DsType.monoS, color = Ds.muted)
                }
                Spacer(Modifier.height(Ds.s3))
                Text("Token", style = DsType.label, color = Ds.ink2)
                Spacer(Modifier.height(6.dp))
                DsTextField(token, { token = it; check = Check.Idle }, "API_TOKEN from .env", secret = !showToken, mono = true,
                    keyboard = KeyboardOptions(keyboardType = KeyboardType.Password, imeAction = ImeAction.Done, autoCorrectEnabled = false)) {
                    IconButton({ showToken = !showToken }) {
                        Icon(if (showToken) Icons.Outlined.VisibilityOff else Icons.Outlined.Visibility, if (showToken) "Hide token" else "Show token", tint = Ds.ink2)
                    }
                }
                Spacer(Modifier.height(6.dp))
                Text("Stored encrypted on this phone.", style = DsType.bodyS, color = Ds.muted)
                Spacer(Modifier.height(Ds.s4))
                DsButton("Test connection", ::test, Modifier.fillMaxWidth(), style = BtnStyle.DARK, enabled = normalized != null, loading = check == Check.Running)
                (check as? Check.Done)?.let { r ->
                    Spacer(Modifier.height(Ds.s4))
                    if (r.error != null) Text(r.error, style = DsType.bodyS, color = Ds.rust)
                    else Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Chip("Backend reachable", Tone.LIVE, glyph = "✓")
                        when (r.tokenOk) {
                            true -> Chip("Token accepted", Tone.LIVE, glyph = "✓")
                            false -> Chip("Token rejected", Tone.ATTENTION, glyph = "!")
                            null -> Chip("Token not checked", Tone.WARN, glyph = "?")
                        }
                        r.health?.detectorReachable?.let {
                            if (it) Chip("Damage detection online", Tone.LIVE, glyph = "✓")
                            else Chip("Damage detection offline · runs later", Tone.WARN, glyph = "!")
                        }
                    }
                }
            }

            // ---------------------------------------------------------------- site
            DsCard {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Eyebrow("Site")
                        Spacer(Modifier.height(10.dp))
                        Text("What are you inspecting?", style = DsType.displayS, color = Ds.ink)
                    }
                    if (normalized != null) RoundIconButton(Icons.Outlined.Refresh, "Reload sites", ::loadMaps)
                }
                Spacer(Modifier.height(Ds.s4))
                when {
                    loadingMaps -> Row(verticalAlignment = Alignment.CenterVertically) {
                        CircularProgressIndicator(Modifier.size(18.dp), color = Ds.blue, strokeWidth = 2.dp)
                        Spacer(Modifier.width(10.dp)); Text("Loading sites", style = DsType.bodyS, color = Ds.ink2)
                    }
                    mapsError != null -> Text(mapsError!!, style = DsType.bodyS, color = Ds.rust)
                    maps == null -> Text("Connect to the backend to see its sites.", style = DsType.bodyS, color = Ds.ink2)
                    maps!!.isEmpty() -> Text("The backend has no scanned sites yet. Register one with scripts/register-map.sh.", style = DsType.bodyS, color = Ds.ink2)
                    else -> Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        maps!!.forEach { m ->
                            SiteRow(m, selected = m.id == settings.mapId) {
                                graph.settings.update { it.copy(mapId = m.id, mapName = m.name) }
                            }
                        }
                    }
                }
                if (settings.mapId.isNotBlank() && maps?.none { it.id == settings.mapId } != false) {
                    Spacer(Modifier.height(10.dp))
                    Text("Current site: ${settings.mapId}", style = DsType.monoS, color = Ds.ink2)
                }
            }

            // ---------------------------------------------------------------- recording
            DsCard {
                Eyebrow("Recording")
                Spacer(Modifier.height(Ds.s3))
                Text("Frame rate", style = DsType.label, color = Ds.ink2)
                Spacer(Modifier.height(8.dp))
                Options(listOf(3.0, 5.0, 10.0), settings.fps, { "${it.toInt()} fps" }) { v -> graph.settings.update { it.copy(fps = v) } }
                Spacer(Modifier.height(Ds.s4))
                Text("Maximum length", style = DsType.label, color = Ds.ink2)
                Spacer(Modifier.height(8.dp))
                Options(listOf(10.0, 20.0, 30.0, 60.0), settings.maxDuration, { "${it.toInt()} s" }) { v -> graph.settings.update { it.copy(maxDuration = v) } }
                Spacer(Modifier.height(Ds.s3))
                Divider()
                SettingSwitch("Show tracked points", "Dots on the walls show what the phone recognises.", settings.showPoints) { v ->
                    graph.settings.update { it.copy(showPoints = v) }
                }
                SettingSwitch("Add a GPS hint", "Helps find the right building. Not needed indoors.", settings.recordGps) { v ->
                    if (v) locationPermission.launch(Manifest.permission.ACCESS_FINE_LOCATION) else graph.settings.update { it.copy(recordGps = false) }
                }
                Spacer(Modifier.height(Ds.s2))
                Text("Recorded by", style = DsType.label, color = Ds.ink2)
                Spacer(Modifier.height(6.dp))
                DsTextField(recorder, { recorder = it; graph.settings.update { s -> s.copy(recorder = it.trim()) } }, "Your name, shown with each record",
                    keyboard = KeyboardOptions(imeAction = ImeAction.Done))
            }

            // ---------------------------------------------------------------- uploads
            DsCard {
                Eyebrow("Uploads")
                Spacer(Modifier.height(4.dp))
                SettingSwitch("Upload automatically", "Send each capture as soon as it is saved.", settings.autoUpload) { v ->
                    graph.settings.update { it.copy(autoUpload = v) }
                }
                SettingSwitch("Only on Wi-Fi", "Captures are 30 to 60 MB each.", settings.wifiOnly) { v ->
                    graph.settings.update { it.copy(wifiOnly = v) }
                }
            }

            // ---------------------------------------------------------------- about
            DsCard {
                Eyebrow("About")
                Spacer(Modifier.height(6.dp))
                DetailRow("App", "deepsplat capture ${BuildConfig.VERSION_NAME}")
                DetailRow("Device", "${android.os.Build.MANUFACTURER} ${android.os.Build.MODEL}")
                DetailRow("Install ID", graph.uploads.deviceId, mono = true)
            }
            Spacer(Modifier.height(Ds.s6))
        }
    }
}

@Composable
private fun SiteRow(m: MapSummary, selected: Boolean, onSelect: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().clip(Ds.rMd)
            .background(if (selected) Ds.blueSoft else Ds.paper)
            .border(if (selected) 1.5.dp else 1.dp, if (selected) Ds.blue else Ds.line, Ds.rMd)
            .clickable(role = Role.RadioButton, onClick = onSelect)
            .padding(14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(Modifier.size(20.dp).clip(CircleShape).border(1.5.dp, if (selected) Ds.blue else Ds.line2, CircleShape), contentAlignment = Alignment.Center) {
            if (selected) Box(Modifier.size(10.dp).clip(CircleShape).background(Ds.blue))
        }
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(m.name, style = DsType.body.copy(fontWeight = FontWeight.SemiBold), color = Ds.ink)
            Text(listOfNotNull(m.property, m.floorLabel, m.id).joinToString(" · "), style = DsType.monoS, color = Ds.ink2)
        }
        Spacer(Modifier.width(8.dp))
        when {
            m.status != "ready" -> Chip("Preparing", Tone.NEUTRAL, glyph = "◌")
            m.vpsStatus == "ready" -> Chip("Placement ready", Tone.LIVE, glyph = "✓")
            m.vpsStatus == "building" -> Chip("Preparing", Tone.INFO, glyph = "◌")
            else -> Chip("No placement", Tone.WARN, glyph = "!")
        }
    }
}

@Composable
private fun Options(values: List<Double>, current: Double, label: (Double) -> String, onPick: (Double) -> Unit) {
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        values.forEach { v ->
            val on = kotlin.math.abs(v - current) < 1e-6
            Box(
                Modifier.clip(Ds.rPill).background(if (on) Ds.ink else Ds.paper)
                    .border(1.dp, if (on) Color.Transparent else Ds.line2, Ds.rPill)
                    .clickable(role = Role.RadioButton) { onPick(v) }.padding(horizontal = 16.dp, vertical = 10.dp),
            ) { Text(label(v), style = DsType.label, color = if (on) Color.White else Ds.ink) }
        }
    }
}
