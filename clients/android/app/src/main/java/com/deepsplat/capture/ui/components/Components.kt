package com.deepsplat.capture.ui.components

import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import com.deepsplat.capture.R
import com.deepsplat.capture.ui.theme.Ds
import com.deepsplat.capture.ui.theme.DsType

enum class BtnStyle { PRIMARY, DARK, SECONDARY, GHOST, ON_DARK_PRIMARY, ON_DARK_SECONDARY }

/** ds-btn: pill, 48 dp (the 44 px web control plus Android's touch target), verb first. */
@Composable
fun DsButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    style: BtnStyle = BtnStyle.PRIMARY,
    icon: ImageVector? = null,
    trailing: ImageVector? = null,
    enabled: Boolean = true,
    loading: Boolean = false,
    small: Boolean = false,
) {
    val (bg, fg, border) = when (style) {
        BtnStyle.PRIMARY -> Triple(Ds.blue, Color.White, null)
        BtnStyle.DARK -> Triple(Ds.ink, Color.White, null)
        BtnStyle.SECONDARY -> Triple(Ds.card, Ds.ink, Ds.line2)
        BtnStyle.GHOST -> Triple(Color.Transparent, Ds.blue, null)
        BtnStyle.ON_DARK_PRIMARY -> Triple(Ds.pick, Ds.ink, null)
        BtnStyle.ON_DARK_SECONDARY -> Triple(Ds.vp2, Ds.vpInk, Ds.vpLine)
    }
    Row(
        modifier
            .heightIn(min = if (small) 36.dp else 48.dp)
            .clip(Ds.rPill)
            .background(bg)
            .then(if (border != null) Modifier.border(1.dp, border, Ds.rPill) else Modifier)
            .clickable(enabled = enabled && !loading, role = Role.Button, onClick = onClick)
            .alpha(if (enabled) 1f else 0.45f)
            .padding(horizontal = if (small) 14.dp else 20.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.Center,
    ) {
        if (loading) {
            CircularProgressIndicator(Modifier.size(16.dp), color = fg, strokeWidth = 2.dp)
            Spacer(Modifier.width(10.dp))
        } else if (icon != null) {
            Icon(icon, null, Modifier.size(18.dp), tint = fg)
            Spacer(Modifier.width(8.dp))
        }
        Text(text, style = DsType.button, color = fg)
        if (trailing != null) {
            Spacer(Modifier.weight(1f, fill = false).width(8.dp))
            Icon(trailing, null, Modifier.size(18.dp), tint = fg)
        }
    }
}

@Composable
fun DsCard(
    modifier: Modifier = Modifier,
    sunken: Boolean = false,
    padding: PaddingValues = PaddingValues(Ds.s5),
    onClick: (() -> Unit)? = null,
    content: @Composable ColumnScope.() -> Unit,
) {
    Column(
        modifier
            .clip(Ds.rCard)
            .background(if (sunken) Ds.paper2 else Ds.card)
            .then(if (sunken) Modifier else Modifier.border(1.dp, Ds.line, Ds.rCard))
            .then(if (onClick != null) Modifier.clickable(onClick = onClick) else Modifier)
            .padding(padding),
        content = content,
    )
}

/** Panel over the dark viewport: --vp-2 with a --vp-line border. */
enum class Tone { LIVE, INFO, SELECTED, WARN, ATTENTION, NEUTRAL }

/** ds-chip / ds-status: glyph plus label on a soft pill. Never colour alone. */
@Composable
fun Chip(text: String, tone: Tone, modifier: Modifier = Modifier, glyph: String? = null, onDark: Boolean = false) {
    val (bg, fg) = if (onDark) when (tone) {
        Tone.LIVE -> Ds.vp2 to Color(0xFF8FD3A6)
        Tone.WARN -> Ds.vp2 to Color(0xFFF0C766)
        Tone.ATTENTION -> Ds.vp2 to Ds.vpRust
        Tone.INFO, Tone.SELECTED -> Ds.vp2 to Ds.pick
        Tone.NEUTRAL -> Ds.vp2 to Ds.vpInk
    } else when (tone) {
        Tone.LIVE -> Ds.greenSoft to Ds.green
        Tone.INFO -> Ds.blueSoft to Ds.blue
        Tone.SELECTED -> Ds.ink to Color.White
        Tone.WARN -> Ds.amberSoft to Ds.amber
        Tone.ATTENTION -> Ds.rustSoft to Ds.rust
        Tone.NEUTRAL -> Ds.paper2 to Ds.ink2
    }
    Row(
        modifier
            .clip(Ds.rPill)
            .background(bg)
            .then(if (onDark) Modifier.border(1.dp, Ds.vpLine, Ds.rPill) else Modifier)
            .padding(horizontal = 10.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (tone == Tone.LIVE && glyph == null) {
            PulseDot(fg)
            Spacer(Modifier.width(6.dp))
        } else if (glyph != null) {
            Text(glyph, style = DsType.label, color = fg)
            Spacer(Modifier.width(5.dp))
        }
        Text(text, style = DsType.label.copy(fontWeight = androidx.compose.ui.text.font.FontWeight.SemiBold), color = fg, maxLines = 1)
    }
}

@Composable
fun PulseDot(color: Color, size: Dp = 7.dp) {
    val t = rememberInfiniteTransition(label = "pulse")
    val a by t.animateFloat(0.35f, 1f, infiniteRepeatable(tween(900), RepeatMode.Reverse), label = "a")
    Box(Modifier.size(size).alpha(a).clip(CircleShape).background(color))
}

/** ds-nav: segmented pills, the current item ink with white text. */
/** Pick reticle: 2 dp ring, four short ticks, centre dot. */
@Composable
fun Reticle(modifier: Modifier = Modifier, color: Color = Ds.pick, active: Boolean = false) {
    val t = rememberInfiniteTransition(label = "reticle")
    val pulse by t.animateFloat(0f, 1f, infiniteRepeatable(tween(1600), RepeatMode.Restart), label = "p")
    Canvas(modifier) {
        val c = Offset(size.width / 2, size.height / 2)
        val r = size.minDimension * 0.32f
        val stroke = 2.dp.toPx()
        drawCircle(color, r, c, style = Stroke(stroke))
        val tick = size.minDimension * 0.1f
        listOf(Offset(0f, -1f), Offset(0f, 1f), Offset(-1f, 0f), Offset(1f, 0f)).forEach { d ->
            drawLine(color, c + d * (r + 4.dp.toPx()), c + d * (r + 4.dp.toPx() + tick), stroke, StrokeCap.Round)
        }
        drawCircle(color, 3.dp.toPx(), c)
        if (active) drawCircle(color.copy(alpha = (1 - pulse) * 0.6f), r * (1 + pulse * 0.45f), c, style = Stroke(stroke))
    }
}

@Composable
fun Wordmark(modifier: Modifier = Modifier, dark: Boolean = false) {
    Image(
        painterResource(if (dark) R.drawable.wordmark_small_dark else R.drawable.wordmark_small_light),
        contentDescription = "deepsplat",
        modifier = modifier.height(22.dp),
    )
}

@Composable
fun DsTextField(
    value: String,
    onValueChange: (String) -> Unit,
    placeholder: String,
    modifier: Modifier = Modifier,
    secret: Boolean = false,
    mono: Boolean = false,
    keyboard: KeyboardOptions = KeyboardOptions.Default,
    trailing: @Composable (RowScope.() -> Unit)? = null,
) {
    var focused by remember { mutableStateOf(false) }
    val style: TextStyle = (if (mono) DsType.mono.copy(fontSize = DsType.body.fontSize) else DsType.body).copy(color = Ds.ink)
    Row(
        modifier
            .heightIn(min = 48.dp)
            .clip(Ds.rPill)
            .background(Ds.paper)
            .border(if (focused) BorderStroke(1.5.dp, Ds.blue) else BorderStroke(1.dp, Ds.line2), Ds.rPill)
            .padding(start = 18.dp, end = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(Modifier.weight(1f).padding(vertical = 12.dp)) {
            if (value.isEmpty()) Text(placeholder, style = style.copy(color = Ds.muted), maxLines = 1)
            BasicTextField(
                value, onValueChange, Modifier.fillMaxWidth().onFocusChanged { focused = it.isFocused },
                textStyle = style, singleLine = true, keyboardOptions = keyboard,
                visualTransformation = if (secret) PasswordVisualTransformation() else VisualTransformation.None,
                cursorBrush = SolidColor(Ds.blue),
            )
        }
        trailing?.invoke(this)
    }
}

@Composable
fun SettingSwitch(title: String, detail: String?, checked: Boolean, onChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth().clickable(role = Role.Switch) { onChange(!checked) }.padding(vertical = 2.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(title, style = DsType.body, color = Ds.ink)
            if (detail != null) Text(detail, style = DsType.bodyS, color = Ds.ink2)
        }
        Spacer(Modifier.width(12.dp))
        Switch(
            checked, onChange,
            colors = SwitchDefaults.colors(
                checkedTrackColor = Ds.blue, checkedThumbColor = Color.White,
                uncheckedTrackColor = Ds.paper2, uncheckedThumbColor = Ds.card, uncheckedBorderColor = Ds.line2,
            ),
        )
    }
}

@Composable
fun Divider(modifier: Modifier = Modifier, color: Color = Ds.line) {
    Box(modifier.fillMaxWidth().height(1.dp).background(color))
}

@Composable
fun RoundIconButton(icon: ImageVector, description: String, onClick: () -> Unit, modifier: Modifier = Modifier, onDark: Boolean = false, enabled: Boolean = true) {
    Box(
        modifier.size(44.dp).clip(CircleShape)
            .background(if (onDark) Ds.vp2.copy(alpha = 0.85f) else Ds.card)
            .border(1.dp, if (onDark) Ds.vpLine else Ds.line, CircleShape)
            .clickable(enabled = enabled, role = Role.Button, onClickLabel = description, onClick = onClick)
            .alpha(if (enabled) 1f else 0.4f),
        contentAlignment = Alignment.Center,
    ) { Icon(icon, description, Modifier.size(20.dp), tint = if (onDark) Ds.vpInk else Ds.ink) }
}
