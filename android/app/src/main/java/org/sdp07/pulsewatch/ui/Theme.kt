package org.sdp07.pulsewatch.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

// a night-shift instrument panel: deep ink, one calm teal for "you're fine",
// amber as the line gets close, coral only when the band actually flags
object Pw {
    val ink = Color(0xFF0E1116)
    val panel = Color(0xFF161B22)
    val panelHi = Color(0xFF1D232D)
    val line = Color(0xFF2A3340)
    val text = Color(0xFFE8ECF2)
    val dim = Color(0xFF8C95A3)
    val faint = Color(0xFF5A6472)
    val calm = Color(0xFF4FD1B5)
    val near = Color(0xFFF2B84B)
    val alert = Color(0xFFFF6B6B)
    val heart = Color(0xFFFF5A6E)
    val master = Color(0xFF6CA8FF)
}

private val scheme = darkColorScheme(
    primary = Pw.calm,
    onPrimary = Color(0xFF00241C),
    secondary = Pw.master,
    background = Pw.ink,
    onBackground = Pw.text,
    surface = Pw.panel,
    onSurface = Pw.text,
    surfaceVariant = Pw.panelHi,
    onSurfaceVariant = Pw.dim,
    outline = Pw.line,
    error = Pw.alert,
)

private val type = Typography(
    displayLarge = TextStyle(fontFamily = FontFamily.SansSerif, fontWeight = FontWeight.SemiBold,
        fontSize = 56.sp, lineHeight = 60.sp, letterSpacing = (-1.5).sp),
    headlineSmall = TextStyle(fontFamily = FontFamily.SansSerif, fontWeight = FontWeight.SemiBold,
        fontSize = 22.sp, lineHeight = 28.sp),
    titleMedium = TextStyle(fontFamily = FontFamily.SansSerif, fontWeight = FontWeight.SemiBold,
        fontSize = 16.sp, lineHeight = 22.sp),
    bodyMedium = TextStyle(fontFamily = FontFamily.SansSerif, fontSize = 14.sp, lineHeight = 20.sp),
    labelSmall = TextStyle(fontFamily = FontFamily.SansSerif, fontWeight = FontWeight.Medium,
        fontSize = 11.sp, lineHeight = 14.sp, letterSpacing = 0.8.sp),
)

@Composable
fun PulseTheme(content: @Composable () -> Unit) =
    MaterialTheme(colorScheme = scheme, typography = type, content = content)
