package com.deepsplat.capture.ui.screens

import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
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
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowForward
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.deepsplat.capture.ui.components.DsButton
import com.deepsplat.capture.ui.components.Eyebrow
import com.deepsplat.capture.ui.components.Wordmark
import com.deepsplat.capture.ui.theme.Ds
import com.deepsplat.capture.ui.theme.DsType
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.sin
import kotlin.random.Random

@Composable
fun OnboardingScreen(onConnect: () -> Unit) {
    Column(
        Modifier.fillMaxSize().background(Ds.paper).windowInsetsPadding(WindowInsets.safeDrawing)
            .verticalScroll(rememberScrollState()).padding(horizontal = Ds.s5, vertical = Ds.s4),
    ) {
        Wordmark()
        Spacer(Modifier.height(Ds.s6))
        ScanHero(Modifier.fillMaxWidth().height(260.dp))
        Spacer(Modifier.height(Ds.s8))
        Eyebrow("Field capture")
        Spacer(Modifier.height(12.dp))
        Text("Record defects where they are.", style = DsType.displayL, color = Ds.ink)
        Spacer(Modifier.height(12.dp))
        Text(
            "Sweep the room, photograph the defect, and the backend places it on the exact spot in the 3D scan of the building.",
            style = DsType.body, color = Ds.ink2,
        )
        Spacer(Modifier.height(Ds.s6))
        Column(verticalArrangement = Arrangement.spacedBy(14.dp)) {
            Step(1, "Connect your backend", "Address and token from the person who runs it.")
            Step(2, "Choose the site", "The building or unit that was scanned.")
            Step(3, "Record a sweep and a photo", "About 20 seconds. Uploads run in the background.")
        }
        Spacer(Modifier.height(Ds.s8))
        DsButton("Connect backend", onConnect, Modifier.fillMaxWidth(), trailing = Icons.AutoMirrored.Outlined.ArrowForward)
        Spacer(Modifier.height(Ds.s4))
    }
}

@Composable
private fun Step(n: Int, title: String, detail: String) {
    Row(verticalAlignment = Alignment.Top) {
        Box(Modifier.size(28.dp).clip(CircleShape).background(Ds.card).border(1.dp, Ds.line2, CircleShape), contentAlignment = Alignment.Center) {
            Text("$n", style = DsType.monoS, color = Ds.blue)
        }
        Spacer(Modifier.width(14.dp))
        Column {
            Text(title, style = DsType.body.copy(fontWeight = FontWeight.SemiBold), color = Ds.ink)
            Text(detail, style = DsType.bodyS, color = Ds.ink2)
        }
    }
}

/** A room drawn as tracked points, a beam from the phone and the defect glowing in rust (the app icon, alive). */
@Composable
private fun ScanHero(modifier: Modifier) {
    val pts = remember {
        val r = Random(7)
        List(260) {
            // Points on three walls of a room in a simple perspective.
            when (r.nextInt(3)) {
                0 -> Offset(0.18f + r.nextFloat() * 0.64f, 0.16f + r.nextFloat() * 0.6f)
                1 -> { val t = r.nextFloat(); Offset(0.18f * t, 0.05f + 0.11f * (1 - t) + r.nextFloat() * (0.6f + 0.3f * (1 - t))) }
                else -> { val t = r.nextFloat(); Offset(0.82f + 0.18f * t, 0.16f - 0.11f * t + r.nextFloat() * (0.6f + 0.3f * t)) }
            } to r.nextFloat()
        }
    }
    val tr = rememberInfiniteTransition(label = "hero")
    val sweep by tr.animateFloat(0f, 1f, infiniteRepeatable(tween(5200, easing = LinearEasing), RepeatMode.Reverse), label = "s")
    val glow by tr.animateFloat(0f, 1f, infiniteRepeatable(tween(1400), RepeatMode.Reverse), label = "g")
    Box(modifier.clip(Ds.rCard).background(Brush.verticalGradient(listOf(Ds.vp2, Ds.vp))).border(1.dp, Ds.vpLine, Ds.rCard)) {
        Canvas(Modifier.fillMaxSize()) {
            val w = size.width; val h = size.height
            val scanX = sweep
            pts.forEach { (p, seed) ->
                val seen = p.x <= scanX + 0.05f
                val a = if (seen) 0.35f + 0.6f * seed else 0.08f
                drawCircle(Ds.pick.copy(alpha = a), (if (seen) 2.2f else 1.6f) * density, Offset(p.x * w, p.y * h))
            }
            val defect = Offset(0.56f * w, 0.46f * h)
            val phone = Offset((0.2f + 0.6f * sweep) * w, 0.95f * h)
            drawLine(Brush.linearGradient(listOf(Ds.pick.copy(alpha = 0.9f), Ds.pick.copy(alpha = 0.1f)), phone, defect), phone, defect, 2f * density)
            drawCircle(Ds.vpRust.copy(alpha = 0.25f + 0.25f * glow), (14 + 6 * glow) * density, defect)
            drawCircle(Ds.vpRust, 5f * density, defect)
            val r = 28f * density
            drawCircle(Ds.pick, r, defect, style = Stroke(2f * density))
            for (i in 0 until 4) {
                val ang = i * PI.toFloat() / 2
                val d = Offset(cos(ang), sin(ang))
                drawLine(Ds.pick, defect + d * (r + 4 * density), defect + d * (r + 11 * density), 2f * density)
            }
            drawCircle(Ds.vpInk, 4f * density, phone)
        }
        Row(Modifier.align(Alignment.BottomStart).padding(16.dp).clip(Ds.rPill).background(Ds.vp2).border(1.dp, Ds.vpLine, Ds.rPill).padding(horizontal = 12.dp, vertical = 7.dp)) {
            Text("Crack · north wall · open", style = DsType.label.copy(fontWeight = FontWeight.SemiBold), color = Ds.vpRust)
        }
    }
}

