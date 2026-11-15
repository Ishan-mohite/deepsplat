package com.deepsplat.capture.ui.screens

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.opengl.GLSurfaceView
import android.provider.Settings
import android.view.WindowManager
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
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
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.FiberManualRecord
import androidx.compose.material.icons.outlined.PhotoCamera
import androidx.compose.material.icons.outlined.Stop
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import com.deepsplat.capture.AppGraph
import com.deepsplat.capture.capture.CaptureEngine
import com.deepsplat.capture.capture.CaptureUi
import com.deepsplat.capture.capture.Phase
import com.deepsplat.capture.ui.clock
import com.deepsplat.capture.ui.components.BtnStyle
import com.deepsplat.capture.ui.components.Chip
import com.deepsplat.capture.ui.components.DsButton
import com.deepsplat.capture.ui.components.Eyebrow
import com.deepsplat.capture.ui.components.PulseDot
import com.deepsplat.capture.ui.components.Reticle
import com.deepsplat.capture.ui.components.RoundIconButton
import com.deepsplat.capture.ui.components.Tone
import com.deepsplat.capture.ui.components.VpPanel
import com.deepsplat.capture.ui.trackingLabel
import com.deepsplat.capture.ui.theme.Ds
import com.deepsplat.capture.ui.theme.DsType

@Composable
fun RecordScreen(graph: AppGraph, onClose: () -> Unit, onSaved: (String) -> Unit, onSettings: () -> Unit) {
    val context = LocalContext.current
    var granted by remember { mutableStateOf(ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) }
    var asked by remember { mutableStateOf(false) }
    val gpsWanted = graph.settings.value.recordGps
    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { result ->
        granted = result[Manifest.permission.CAMERA] == true
        asked = true
    }
    LaunchedEffect(Unit) {
        if (!granted) launcher.launch(
            if (gpsWanted) arrayOf(Manifest.permission.CAMERA, Manifest.permission.ACCESS_FINE_LOCATION) else arrayOf(Manifest.permission.CAMERA)
        )
    }
    Box(Modifier.fillMaxSize().background(Ds.vp)) {
        if (granted) Recorder(graph, onClose, onSaved, onSettings)
        else if (asked) PermissionNeeded(onClose) {
            context.startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", context.packageName, null)))
        }
    }
}

@Composable
private fun PermissionNeeded(onClose: () -> Unit, onOpenSettings: () -> Unit) {
    Column(Modifier.fillMaxSize().windowInsetsPadding(WindowInsets.safeDrawing).padding(Ds.s6), verticalArrangement = Arrangement.Center) {
        Eyebrow("Camera access", color = Ds.pick)
        Spacer(Modifier.height(12.dp))
        Text("The camera records the room and the defect.", style = DsType.displayM, color = Ds.vpInk)
        Spacer(Modifier.height(10.dp))
        Text("Allow camera access in the system settings to record captures. Nothing is shared until you upload a capture.", style = DsType.body, color = Ds.vpMuted)
        Spacer(Modifier.height(Ds.s6))
        DsButton("Open settings", onOpenSettings, Modifier.fillMaxWidth(), style = BtnStyle.ON_DARK_PRIMARY)
        Spacer(Modifier.height(10.dp))
        DsButton("Not now", onClose, Modifier.fillMaxWidth(), style = BtnStyle.ON_DARK_SECONDARY)
    }
}

@Composable
private fun Recorder(graph: AppGraph, onClose: () -> Unit, onSaved: (String) -> Unit, onSettings: () -> Unit) {
    val context = LocalContext.current
    val activity = context as Activity
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    val haptic = LocalHapticFeedback.current
    val settings = remember { graph.settings.value }
    val engine = remember { CaptureEngine(context.applicationContext, graph, settings.capture).also { it.showPoints = settings.showPoints } }
    val ui by engine.ui.collectAsState()
    var confirmDiscard by remember { mutableStateOf(false) }
    val glView = remember {
        GLSurfaceView(context).apply {
            preserveEGLContextOnPause = true
            setEGLContextClientVersion(2)
            setEGLConfigChooser(8, 8, 8, 8, 16, 0)
            setRenderer(engine)
            renderMode = GLSurfaceView.RENDERMODE_CONTINUOUSLY
            setWillNotDraw(false)
        }
    }

    DisposableEffect(lifecycle) {
        activity.window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        val observer = LifecycleEventObserver { _, e ->
            when (e) {
                Lifecycle.Event.ON_RESUME -> if (engine.resume(activity)) glView.onResume()
                Lifecycle.Event.ON_PAUSE -> { glView.onPause(); engine.pause() }
                else -> Unit
            }
        }
        lifecycle.addObserver(observer)
        onDispose {
            lifecycle.removeObserver(observer)
            glView.onPause()
            engine.close()
            activity.window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        }
    }

    LaunchedEffect(ui.savedId) { ui.savedId?.let { haptic.performHapticFeedback(HapticFeedbackType.LongPress); onSaved(it) } }
    LaunchedEffect(ui.hasPhoto) { if (ui.hasPhoto) haptic.performHapticFeedback(HapticFeedbackType.TextHandleMove) }

    val recording = ui.phase == Phase.RECORDING
    BackHandler {
        if (recording || ui.phase == Phase.SAVING) confirmDiscard = true else onClose()
    }

    Box(Modifier.fillMaxSize()) {
        AndroidView({ glView }, Modifier.fillMaxSize())
        // Scrims keep the overlays legible over any scene.
        Box(Modifier.fillMaxWidth().height(200.dp).background(Brush.verticalGradient(listOf(Ds.vp.copy(alpha = 0.75f), Color.Transparent))))
        Box(Modifier.align(Alignment.BottomCenter).fillMaxWidth().height(360.dp).background(Brush.verticalGradient(listOf(Color.Transparent, Ds.vp.copy(alpha = 0.85f)))))

        Reticle(
            Modifier.align(Alignment.Center).offset(y = (-60).dp).size(132.dp).alpha(if (ui.phase == Phase.STARTING) 0.4f else 1f),
            color = if (ui.takingPhoto) Ds.vpInk else Ds.pick, active = ui.tracking == "normal",
        )

        Column(Modifier.fillMaxSize().windowInsetsPadding(WindowInsets.safeDrawing).padding(horizontal = Ds.s5, vertical = Ds.s3)) {
            TopBar(ui, settings.mapName.ifBlank { settings.mapId }, onClose = { if (recording) confirmDiscard = true else onClose() })
            AnimatedVisibility(ui.trackingWarning && recording, enter = fadeIn(), exit = fadeOut()) {
                VpPanel(Modifier.padding(top = 12.dp).fillMaxWidth(), shape = Ds.rMd, padding = PaddingValues(14.dp)) {
                    Text("Tracking is limited. Move slowly and keep textured surfaces in view.", style = DsType.bodyS, color = Color(0xFFF0C766))
                }
            }
            Spacer(Modifier.weight(1f))
            if (ui.takingPhoto) Text("Hold still", style = DsType.displayS, color = Ds.vpInk, modifier = Modifier.align(Alignment.CenterHorizontally).padding(bottom = 16.dp))
            ControlPanel(ui, settings.capture.maxDuration, settings.mapId.isBlank(),
                onStart = { haptic.performHapticFeedback(HapticFeedbackType.LongPress); engine.start() },
                onPhoto = { haptic.performHapticFeedback(HapticFeedbackType.LongPress); engine.takePhoto() },
                onStop = { engine.stop() },
                onSettings = onSettings,
            )
        }
    }

    if (confirmDiscard) AlertDialog(
        onDismissRequest = { confirmDiscard = false },
        title = { Text("Discard this capture?", style = DsType.displayS) },
        text = { Text("The frames recorded so far are deleted from this phone.", style = DsType.body) },
        confirmButton = { TextButton({ confirmDiscard = false; engine.discard(); onClose() }) { Text("Discard capture", color = Ds.rust, style = DsType.button) } },
        dismissButton = { TextButton({ confirmDiscard = false }) { Text("Keep recording", color = Ds.ink, style = DsType.button) } },
        containerColor = Ds.card, shape = Ds.rCard,
    )
    ui.message?.let { m ->
        AlertDialog(
            onDismissRequest = engine::consumeMessage,
            title = { Text("Capture", style = DsType.displayS) },
            text = { Text(m, style = DsType.body) },
            confirmButton = { TextButton(engine::consumeMessage) { Text("OK", color = Ds.blue, style = DsType.button) } },
            containerColor = Ds.card, shape = Ds.rCard,
        )
    }
    ui.fatal?.let { m ->
        AlertDialog(
            onDismissRequest = onClose,
            title = { Text("Recording is not available", style = DsType.displayS) },
            text = { Text(m, style = DsType.body) },
            confirmButton = { TextButton(onClose) { Text("Close", color = Ds.blue, style = DsType.button) } },
            containerColor = Ds.card, shape = Ds.rCard,
        )
    }
}

@Composable
private fun TopBar(ui: CaptureUi, site: String, onClose: () -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        RoundIconButton(Icons.Outlined.Close, "Close capture", onClose, onDark = true)
        Spacer(Modifier.width(14.dp))
        Column(Modifier.weight(1f)) {
            Eyebrow("Inspection capture", color = Ds.vpMuted)
            Spacer(Modifier.height(4.dp))
            Text(site.ifBlank { "No site chosen" }, style = DsType.body.copy(fontWeight = FontWeight.SemiBold), color = Ds.vpInk, maxLines = 1)
        }
        if (ui.phase == Phase.RECORDING) {
            Row(
                Modifier.clip(Ds.rPill).background(Ds.vp2).border(1.dp, Ds.vpLine, Ds.rPill).padding(horizontal = 12.dp, vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                PulseDot(Ds.vpRust, 8.dp)
                Spacer(Modifier.width(8.dp))
                Text("REC ${clock(ui.elapsed)}", style = DsType.monoS.copy(fontSize = 12.sp), color = Ds.vpInk)
            }
        }
    }
    Spacer(Modifier.height(12.dp))
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
        val (label, tone) = trackingLabel(ui.tracking)
        Chip(label, tone, onDark = true, glyph = if (tone == Tone.LIVE) null else "!".takeIf { tone == Tone.WARN } ?: "◌")
        if (ui.points > 0) Chip("${ui.points} points", Tone.INFO, onDark = true, glyph = "·")
        if (ui.hasPhoto) Chip("Photo saved", Tone.LIVE, onDark = true, glyph = "✓")
    }
}

@Composable
private fun ControlPanel(
    ui: CaptureUi, maxDuration: Double, noSite: Boolean,
    onStart: () -> Unit, onPhoto: () -> Unit, onStop: () -> Unit, onSettings: () -> Unit,
) {
    VpPanel(Modifier.fillMaxWidth()) {
        Steps(ui)
        Spacer(Modifier.height(14.dp))
        Text(instruction(ui, noSite), style = DsType.bodyS, color = Ds.vpMuted, modifier = Modifier.fillMaxWidth())
        Spacer(Modifier.height(Ds.s5))
        when (ui.phase) {
            Phase.STARTING, Phase.READY, Phase.FAILED -> {
                if (noSite) DsButton("Choose site", onSettings, Modifier.fillMaxWidth(), style = BtnStyle.ON_DARK_PRIMARY)
                else DsButton(
                    if (ui.tracking == "normal") "Start recording" else "Waiting for tracking", onStart, Modifier.fillMaxWidth(),
                    style = BtnStyle.ON_DARK_PRIMARY, icon = Icons.Outlined.FiberManualRecord, enabled = ui.tracking == "normal",
                )
                if (ui.imageSize.isNotEmpty()) {
                    Spacer(Modifier.height(10.dp))
                    Text("Camera ${ui.imageSize} · motion data on", style = DsType.monoS, color = Ds.vpMuted, modifier = Modifier.align(Alignment.CenterHorizontally))
                }
            }
            Phase.RECORDING -> Row(verticalAlignment = Alignment.CenterVertically) {
                PhotoSlot(ui, Modifier.weight(1f))
                Shutter(ui, maxDuration, onPhoto)
                Box(Modifier.weight(1f), contentAlignment = Alignment.CenterEnd) {
                    StopButton(enabled = ui.hasPhoto && !ui.takingPhoto, onStop)
                }
            }
            Phase.SAVING, Phase.SAVED -> Row(Modifier.fillMaxWidth().padding(vertical = 12.dp), horizontalArrangement = Arrangement.Center, verticalAlignment = Alignment.CenterVertically) {
                CircularProgressIndicator(Modifier.size(20.dp), color = Ds.pick, strokeWidth = 2.dp)
                Spacer(Modifier.width(12.dp))
                Text("Saving your capture", style = DsType.body, color = Ds.vpInk)
            }
        }
        if (ui.phase == Phase.RECORDING) {
            Spacer(Modifier.height(14.dp))
            Text("${clock(ui.elapsed)} of ${clock(maxDuration)} · ${ui.frames} frames", style = DsType.monoS, color = Ds.vpMuted,
                modifier = Modifier.fillMaxWidth(), textAlign = TextAlign.Center)
        }
    }
}

private fun instruction(ui: CaptureUi, noSite: Boolean): String = when {
    noSite -> "Choose the site you are inspecting before you record."
    ui.phase == Phase.STARTING -> "Starting the camera. Hold the phone upright."
    ui.phase == Phase.READY && ui.tracking != "normal" -> "Move the phone slowly from side to side until tracking is ready."
    ui.phase == Phase.READY -> "Start a few metres away and walk slowly towards the defect while recording."
    ui.phase == Phase.RECORDING && ui.takingPhoto -> "Taking the sharpest of several frames."
    ui.phase == Phase.RECORDING && !ui.hasPhoto -> "Aim the reticle at the defect and tap the shutter for a sharp photo."
    ui.phase == Phase.RECORDING -> "Photo saved. Keep sweeping for context, or stop to review."
    else -> "Keeping your frames, photo and motion data together."
}

@Composable
private fun Steps(ui: CaptureUi) {
    val sweepDone = ui.frames > 10
    val step = when {
        ui.phase == Phase.SAVING || ui.phase == Phase.SAVED -> 3
        ui.hasPhoto -> 3
        ui.phase == Phase.RECORDING -> if (sweepDone) 2 else 1
        else -> 1
    }
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
        listOf("Sweep", "Defect photo", "Review").forEachIndexed { i, title ->
            val n = i + 1
            val done = n < step || (n == 2 && ui.hasPhoto)
            val active = n == step
            Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.weight(1f)) {
                Box(
                    Modifier.size(22.dp).clip(CircleShape)
                        .background(if (done) Ds.pick else if (active) Ds.vpInk else Color.Transparent)
                        .border(1.dp, if (done || active) Color.Transparent else Ds.vpLine, CircleShape),
                    contentAlignment = Alignment.Center,
                ) {
                    Text(if (done) "✓" else "$n", style = DsType.monoS, color = if (done || active) Ds.ink else Ds.vpMuted)
                }
                Spacer(Modifier.width(6.dp))
                Text(title, style = DsType.label, color = if (active || done) Ds.vpInk else Ds.vpMuted, maxLines = 1)
            }
        }
    }
}

@Composable
private fun PhotoSlot(ui: CaptureUi, modifier: Modifier) {
    Box(modifier, contentAlignment = Alignment.CenterStart) {
        val p = ui.photoPreview
        Box(Modifier.size(52.dp).clip(Ds.rSm).background(Ds.vp).border(1.dp, if (p != null) Ds.pick else Ds.vpLine, Ds.rSm), contentAlignment = Alignment.Center) {
            if (p != null) Image(p.asImageBitmap(), "Defect photo", Modifier.fillMaxSize(), contentScale = ContentScale.Crop)
            else Icon(Icons.Outlined.PhotoCamera, null, Modifier.size(20.dp), tint = Ds.vpMuted)
        }
    }
}

/** Shutter for the defect photo with the recording time as a ring around it. */
@Composable
private fun Shutter(ui: CaptureUi, maxDuration: Double, onPhoto: () -> Unit) {
    val fraction by animateFloatAsState((ui.elapsed / maxDuration).toFloat().coerceIn(0f, 1f), tween(200), label = "ring")
    val label = if (ui.takingPhoto) "Taking photo" else if (ui.hasPhoto) "Retake photo" else "Defect photo"
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Box(
            Modifier.size(84.dp).clip(CircleShape).clickable(enabled = !ui.takingPhoto, role = Role.Button, onClick = onPhoto)
                .semantics { contentDescription = label },
            contentAlignment = Alignment.Center,
        ) {
            Canvas(Modifier.fillMaxSize()) {
                val stroke = 4.dp.toPx()
                val inset = stroke / 2
                drawArc(Ds.vpLine, 0f, 360f, false, Offset(inset, inset), Size(size.width - stroke, size.height - stroke), style = Stroke(stroke))
                drawArc(Ds.pick, -90f, 360f * fraction, false, Offset(inset, inset), Size(size.width - stroke, size.height - stroke), style = Stroke(stroke, cap = StrokeCap.Round))
            }
            Box(Modifier.size(64.dp).clip(CircleShape).background(if (ui.takingPhoto) Ds.pick else Ds.vpInk), contentAlignment = Alignment.Center) {
                if (ui.takingPhoto) CircularProgressIndicator(Modifier.size(22.dp), color = Ds.ink, strokeWidth = 2.dp)
                else Icon(Icons.Outlined.PhotoCamera, null, Modifier.size(26.dp), tint = Ds.ink)
            }
        }
        Spacer(Modifier.height(6.dp))
        Text(label, style = DsType.label, color = Ds.vpInk)
    }
}

@Composable
private fun StopButton(enabled: Boolean, onStop: () -> Unit) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Box(
            Modifier.size(52.dp).clip(CircleShape).background(Ds.vp).border(1.dp, Ds.vpLine, CircleShape)
                .clickable(enabled = enabled, role = Role.Button, onClickLabel = "Stop and review", onClick = onStop).alpha(if (enabled) 1f else 0.4f),
            contentAlignment = Alignment.Center,
        ) { Icon(Icons.Outlined.Stop, "Stop", Modifier.size(24.dp), tint = Ds.vpInk) }
        Spacer(Modifier.height(6.dp))
        Text("Stop", style = DsType.label, color = if (enabled) Ds.vpInk else Ds.vpMuted)
    }
}
