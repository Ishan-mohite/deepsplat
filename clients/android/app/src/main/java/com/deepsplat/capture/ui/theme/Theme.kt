package com.deepsplat.capture.ui.theme

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import com.deepsplat.capture.R

/** design/system/tokens.css, one to one. If a value is not here, it is not part of the system. */
object Ds {
    // Surfaces (light product UI)
    val paper = Color(0xFFF1EEE6)
    val paper2 = Color(0xFFE8E2D4)
    val card = Color(0xFFFBFAF7)
    // Text
    val ink = Color(0xFF172033)
    val ink2 = Color(0xFF4A5163)
    val muted = Color(0xFF7D786A)
    // Lines
    val line = Color(0xFFE2DCCF)
    val line2 = Color(0xFFD3CBBB)
    // Roles
    val blue = Color(0xFF2453D4)
    val blueSoft = Color(0xFFE3E9FB)
    val rust = Color(0xFFC24A1C)
    val rustSoft = Color(0xFFF7E5DB)
    val green = Color(0xFF2F7D4B)
    val greenSoft = Color(0xFFE1EFE3)
    val amber = Color(0xFF946300)
    val amberSoft = Color(0xFFF6ECD2)
    // 3D viewport (always dark)
    val vp = Color(0xFF1A2130)
    val vp2 = Color(0xFF232C3E)
    val vpLine = Color(0xFF36435C)
    val vpInk = Color(0xFFE9ECF3)
    val vpMuted = Color(0xFF9AA4B8)
    val pick = Color(0xFF7EA2FF)
    val vpRust = Color(0xFFE8784A)
    // Severity
    val sevLow = Color(0xFF7C8DA6)
    val sevMedium = Color(0xFFC98500)
    val sevHigh = Color(0xFFD95926)
    val sevCritical = Color(0xFFB3261E)

    // Spacing (4 px base)
    val s1 = 4.dp; val s2 = 8.dp; val s3 = 12.dp; val s4 = 16.dp; val s5 = 20.dp; val s6 = 24.dp
    val s8 = 32.dp; val s10 = 40.dp; val s14 = 56.dp; val s18 = 72.dp

    // Radii
    val rCard = RoundedCornerShape(22.dp)
    val rMd = RoundedCornerShape(14.dp)
    val rSm = RoundedCornerShape(9.dp)
    val rPill = RoundedCornerShape(percent = 50)
}

val SpaceGrotesk = FontFamily(
    Font(R.font.space_grotesk_500, FontWeight.Medium),
    Font(R.font.space_grotesk_600, FontWeight.SemiBold),
    Font(R.font.space_grotesk_700, FontWeight.Bold),
)
val PlexSans = FontFamily(
    Font(R.font.ibm_plex_sans_400, FontWeight.Normal),
    Font(R.font.ibm_plex_sans_500, FontWeight.Medium),
    Font(R.font.ibm_plex_sans_600, FontWeight.SemiBold),
)
val PlexMono = FontFamily(
    Font(R.font.ibm_plex_mono_400, FontWeight.Normal),
    Font(R.font.ibm_plex_mono_500, FontWeight.Medium),
)

/** The type scale from DESIGN.md. */
object DsType {
    val displayXl = TextStyle(fontFamily = SpaceGrotesk, fontWeight = FontWeight.Bold, fontSize = 44.sp, lineHeight = 47.5.sp, letterSpacing = (-0.025).em)
    val displayL = TextStyle(fontFamily = SpaceGrotesk, fontWeight = FontWeight.Bold, fontSize = 32.sp, lineHeight = 35.sp, letterSpacing = (-0.02).em)
    val displayM = TextStyle(fontFamily = SpaceGrotesk, fontWeight = FontWeight.SemiBold, fontSize = 24.sp, lineHeight = 27.6.sp, letterSpacing = (-0.015).em)
    val displayS = TextStyle(fontFamily = SpaceGrotesk, fontWeight = FontWeight.SemiBold, fontSize = 18.sp, lineHeight = 22.5.sp, letterSpacing = (-0.01).em)
    val kpi = TextStyle(fontFamily = SpaceGrotesk, fontWeight = FontWeight.SemiBold, fontSize = 40.sp, lineHeight = 40.sp, letterSpacing = (-0.03).em)
    val body = TextStyle(fontFamily = PlexSans, fontWeight = FontWeight.Normal, fontSize = 15.sp, lineHeight = 22.5.sp)
    val bodyS = TextStyle(fontFamily = PlexSans, fontWeight = FontWeight.Normal, fontSize = 13.sp, lineHeight = 18.85.sp)
    val label = TextStyle(fontFamily = PlexSans, fontWeight = FontWeight.Medium, fontSize = 13.sp, lineHeight = 15.6.sp)
    val button = TextStyle(fontFamily = PlexSans, fontWeight = FontWeight.SemiBold, fontSize = 14.sp, lineHeight = 14.sp)
    val eyebrow = TextStyle(fontFamily = SpaceGrotesk, fontWeight = FontWeight.SemiBold, fontSize = 11.sp, lineHeight = 11.sp, letterSpacing = 0.14.em)
    val mono = TextStyle(fontFamily = PlexMono, fontWeight = FontWeight.Normal, fontSize = 13.sp, lineHeight = 18.2.sp)
    val monoS = TextStyle(fontFamily = PlexMono, fontWeight = FontWeight.Medium, fontSize = 11.sp, lineHeight = 14.3.sp)
}

@Composable
fun DeepsplatTheme(content: @Composable () -> Unit) {
    val colors = lightColorScheme(
        primary = Ds.blue, onPrimary = Color.White, primaryContainer = Ds.blueSoft, onPrimaryContainer = Ds.ink,
        secondary = Ds.ink, onSecondary = Color.White,
        tertiary = Ds.green, onTertiary = Color.White,
        error = Ds.rust, onError = Color.White, errorContainer = Ds.rustSoft, onErrorContainer = Ds.ink,
        background = Ds.paper, onBackground = Ds.ink,
        surface = Ds.paper, onSurface = Ds.ink, surfaceVariant = Ds.paper2, onSurfaceVariant = Ds.ink2,
        surfaceContainerLowest = Ds.card, surfaceContainerLow = Ds.card, surfaceContainer = Ds.card,
        surfaceContainerHigh = Ds.card, surfaceContainerHighest = Ds.paper2,
        outline = Ds.line2, outlineVariant = Ds.line,
        scrim = Ds.ink.copy(alpha = 0.4f), inverseSurface = Ds.ink, inverseOnSurface = Ds.paper, inversePrimary = Ds.pick,
    )
    val type = Typography(
        displayLarge = DsType.displayXl, displayMedium = DsType.displayL, displaySmall = DsType.displayM,
        headlineLarge = DsType.displayL, headlineMedium = DsType.displayM, headlineSmall = DsType.displayS,
        titleLarge = DsType.displayS, titleMedium = DsType.body.copy(fontWeight = FontWeight.SemiBold), titleSmall = DsType.label,
        bodyLarge = DsType.body, bodyMedium = DsType.body, bodySmall = DsType.bodyS,
        labelLarge = DsType.button, labelMedium = DsType.label, labelSmall = DsType.monoS,
    )
    MaterialTheme(colorScheme = colors, typography = type, shapes = Shapes(small = Ds.rSm, medium = Ds.rMd, large = Ds.rCard), content = content)
}
