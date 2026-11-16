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
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
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
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Close
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
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import com.deepsplat.capture.AppGraph
import com.deepsplat.capture.capture.CaptureEngine
import com.deepsplat.capture.capture.CaptureUi
import com.deepsplat.capture.capture.Phase
import com.deepsplat.capture.ui.clock
import com.deepsplat.capture.ui.components.BtnStyle
import com.deepsplat.capture.ui.components.Chip
import com.deepsplat.capture.ui.components.DsButton
import com.deepsplat.capture.ui.components.PulseDot
import com.deepsplat.capture.ui.components.Reticle
import com.deepsplat.capture.ui.components.RoundIconButton
import com.deepsplat.capture.ui.components.Tone
import com.deepsplat.capture.ui.trackingLabel
import com.deepsplat.capture.ui.theme.Ds
import com.deepsplat.capture.ui.theme.DsType

@Composable
fun RecordScreen(graph: AppGraph, onClose: () -> Unit, onSaved: (String) -> Unit) {
    val context = LocalContext.current
    var granted by remember { mutableStateOf(ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) }
    var asked by remember { mutableStateOf(false) }
    val gps = graph.settings.value.recordGps
    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { r ->
        granted = r[Manifest.permission.CAMERA] == true; asked = true
    }
    LaunchedEffect(Unit) {
        if (!granted) launcher.launch(if (gps) arrayOf(Manifest.permission.CAMERA, Manifest.permission.ACCESS_FINE_LOCATION) else arrayOf(Manifest.permission.CAMERA))
    }
    Box(Modifier.fillMaxSize().background(Ds.vp)) {
        if (granted) Recorder(graph, onClose, onSaved)
        else if (asked) Column(
            Modifier.fillMaxSize().windowInsetsPadding(WindowInsets.safeDrawing).padding(Ds.s6),
            verticalArrangement = Arrangement.spacedBy(Ds.s3, Alignment.CenterVertically),
        ) {
            Text("Camera access needed", style = DsType.displayS, color = Ds.vpInk)
            DsButton("Open settings", {
                context.startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", context.packageName, null)))
            }, Modifier.fillMaxWidth(), style = BtnStyle.ON_DARK_PRIMARY)
            DsButton("Close", onClose, Modifier.fillMaxWidth(), style = BtnStyle.ON_DARK_SECONDARY)
        }
    }
}

@Composable
private fun Recorder(graph: AppGraph, onClose: () -> Unit, onSaved: (String) -> Unit) {
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
    BackHandler { if (recording || ui.phase == Phase.SAVING) confirmDiscard = true else onClose() }

    Box(Modifier.fillMaxSize()) {
        AndroidView({ glView }, Modifier.fillMaxSize())
        RecordOverlay(ui, settings.capture.maxDuration,
            onClose = { if (recording) confirmDiscard = true else onClose() },
            onStart = { haptic.performHapticFeedback(HapticFeedbackType.LongPress); engine.start() },
            onPhoto = { haptic.performHapticFeedback(HapticFeedbackType.LongPress); engine.takePhoto() },
            onStop = engine::stop,
        )
    }

    if (confirmDiscard) AlertDialog(
        onDismissRequest = { confirmDiscard = false },
        title = { Text("Discard capture?", style = DsType.displayS) },
        confirmButton = { TextButton({ confirmDiscard = false; engine.discard(); onClose() }) { Text("Discard", color = Ds.rust, style = DsType.button) } },
        dismissButton = { TextButton({ confirmDiscard = false }) { Text("Keep", color = Ds.ink, style = DsType.button) } },
        containerColor = Ds.card, shape = Ds.rCard,
    )
    (ui.fatal ?: ui.message)?.let { m ->
        val fatal = ui.fatal != null
        AlertDialog(
            onDismissRequest = { if (fatal) onClose() else engine.consumeMessage() },
            text = { Text(m, style = DsType.body) },
            confirmButton = { TextButton({ if (fatal) onClose() else engine.consumeMessage() }) { Text("OK", color = Ds.blue, style = DsType.button) } },
            containerColor = Ds.card, shape = Ds.rCard,
        )
    }
}

/** Everything drawn over the camera image. */
@Composable
internal fun RecordOverlay(ui: CaptureUi, maxDuration: Double, onClose: () -> Unit, onStart: () -> Unit, onPhoto: () -> Unit, onStop: () -> Unit) {
    val recording = ui.phase == Phase.RECORDING
    Box(Modifier.fillMaxSize()) {
        Box(Modifier.fillMaxWidth().height(140.dp).background(Brush.verticalGradient(listOf(Ds.vp.copy(alpha = 0.6f), Color.Transparent))))
        Box(Modifier.align(Alignment.BottomCenter).fillMaxWidth().height(260.dp).background(Brush.verticalGradient(listOf(Color.Transparent, Ds.vp.copy(alpha = 0.8f)))))
        Reticle(
            Modifier.align(Alignment.Center).offset(y = (-40).dp).size(120.dp).alpha(if (ui.phase == Phase.STARTING) 0.4f else 1f),
            color = if (ui.takingPhoto) Ds.vpInk else Ds.pick, active = ui.tracking == "normal",
        )
        Column(Modifier.fillMaxSize().windowInsetsPadding(WindowInsets.safeDrawing).padding(horizontal = Ds.s5, vertical = Ds.s3)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                RoundIconButton(Icons.Outlined.Close, "Close", onClose, onDark = true)
                Spacer(Modifier.weight(1f))
                if (ui.tracking != "normal" && ui.phase != Phase.SAVING) {
                    val (label, _) = trackingLabel(ui.tracking)
                    Chip(label, Tone.WARN, onDark = true, glyph = "!")
                } else if (recording) RecTimer(ui.elapsed)
            }
            Spacer(Modifier.weight(1f))
            Text(hint(ui), style = DsType.label, color = Ds.vpInk, textAlign = TextAlign.Center, modifier = Modifier.fillMaxWidth())
            Spacer(Modifier.height(Ds.s5))
            Controls(ui, maxDuration, onStart, onPhoto, onStop)
            Spacer(Modifier.height(Ds.s3))
        }
    }
}

@Composable
private fun RecTimer(elapsed: Double) {
    Row(
        Modifier.clip(Ds.rPill).background(Ds.vp2).border(1.dp, Ds.vpLine, Ds.rPill).padding(horizontal = 12.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        PulseDot(Ds.vpRust, 8.dp)
        Spacer(Modifier.width(8.dp))
        Text(clock(elapsed), style = DsType.monoS.copy(fontSize = 13.sp), color = Ds.vpInk)
    }
}

private fun hint(ui: CaptureUi): String = when {
    ui.phase == Phase.SAVING || ui.phase == Phase.SAVED -> "Saving"
    ui.phase != Phase.RECORDING -> if (ui.tracking == "normal") "Walk slowly towards the defect" else "Move the phone slowly"
    ui.takingPhoto -> "Hold still"
    !ui.hasPhoto -> "Photograph the defect"
    else -> "Stop when done"
}

@Composable
private fun Controls(ui: CaptureUi, maxDuration: Double, onStart: () -> Unit, onPhoto: () -> Unit, onStop: () -> Unit) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.weight(1f), contentAlignment = Alignment.CenterStart) {
            if (ui.phase == Phase.RECORDING) PhotoSlot(ui)
        }
        when (ui.phase) {
            Phase.RECORDING -> Shutter(ui, maxDuration, onPhoto)
            Phase.SAVING, Phase.SAVED -> Box(Modifier.size(80.dp), contentAlignment = Alignment.Center) {
                CircularProgressIndicator(Modifier.size(28.dp), color = Ds.pick, strokeWidth = 2.dp)
            }
            else -> RecordButton(enabled = ui.tracking == "normal", onStart)
        }
        Box(Modifier.weight(1f), contentAlignment = Alignment.CenterEnd) {
            if (ui.phase == Phase.RECORDING) {
                val enabled = ui.hasPhoto && !ui.takingPhoto
                Box(
                    Modifier.size(52.dp).clip(CircleShape).background(Ds.vp2).border(1.dp, Ds.vpLine, CircleShape)
                        .clickable(enabled = enabled, role = Role.Button, onClickLabel = "Stop", onClick = onStop).alpha(if (enabled) 1f else 0.35f),
                    contentAlignment = Alignment.Center,
                ) { Icon(Icons.Outlined.Stop, "Stop", Modifier.size(24.dp), tint = Ds.vpInk) }
            }
        }
    }
}

@Composable
private fun RecordButton(enabled: Boolean, onStart: () -> Unit) {
    Box(
        Modifier.size(80.dp).clip(CircleShape).border(4.dp, Ds.vpInk, CircleShape)
            .clickable(enabled = enabled, role = Role.Button, onClickLabel = "Start recording", onClick = onStart)
            .alpha(if (enabled) 1f else 0.4f).padding(9.dp).clip(CircleShape).background(Ds.vpRust),
    )
}

@Composable
private fun PhotoSlot(ui: CaptureUi) {
    val p = ui.photoPreview
    Box(Modifier.size(52.dp).clip(Ds.rSm).background(Ds.vp2).border(1.dp, if (p != null) Ds.pick else Ds.vpLine, Ds.rSm), contentAlignment = Alignment.Center) {
        if (p != null) Image(p.asImageBitmap(), "Defect photo", Modifier.fillMaxSize(), contentScale = ContentScale.Crop)
    }
}

/** Shutter for the defect photo; the ring shows the recording time. */
@Composable
private fun Shutter(ui: CaptureUi, maxDuration: Double, onPhoto: () -> Unit) {
    val fraction by animateFloatAsState((ui.elapsed / maxDuration).toFloat().coerceIn(0f, 1f), tween(200), label = "ring")
    Box(
        Modifier.size(80.dp).clip(CircleShape).clickable(enabled = !ui.takingPhoto, role = Role.Button, onClickLabel = "Take defect photo", onClick = onPhoto),
        contentAlignment = Alignment.Center,
    ) {
        Canvas(Modifier.fillMaxSize()) {
            val s = 4.dp.toPx()
            val o = Offset(s / 2, s / 2)
            val sz = Size(size.width - s, size.height - s)
            drawArc(Ds.vpLine, 0f, 360f, false, o, sz, style = Stroke(s))
            drawArc(Ds.pick, -90f, 360f * fraction, false, o, sz, style = Stroke(s, cap = StrokeCap.Round))
        }
        Box(Modifier.size(62.dp).clip(CircleShape).background(if (ui.takingPhoto) Ds.pick else Ds.vpInk), contentAlignment = Alignment.Center) {
            if (ui.takingPhoto) CircularProgressIndicator(Modifier.size(22.dp), color = Ds.ink, strokeWidth = 2.dp)
            else Icon(Icons.Outlined.PhotoCamera, null, Modifier.size(26.dp), tint = Ds.ink)
        }
    }
}
