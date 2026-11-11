package org.sdp07.pulsewatch.ui

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Share
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.drawIntoCanvas
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.sdp07.pulsewatch.ChartPoint
import org.sdp07.pulsewatch.DaySummary
import org.sdp07.pulsewatch.FlagEvent
import org.sdp07.pulsewatch.History
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

private class DayView(val days: List<Long>, val summary: DaySummary?, val chart: List<ChartPoint>,
                      val events: List<FlagEvent>)

@Composable
fun HistoryScreen(history: History, band: String, onExport: () -> Unit) {
    val version by history.version.collectAsStateWithLifecycle()
    var pick by remember(band) { mutableIntStateOf(0) }
    var view by remember(band) { mutableStateOf<DayView?>(null) }
    LaunchedEffect(band, version, pick) {
        view = withContext(Dispatchers.IO) {
            val days = history.days(band)
            val d = days.getOrNull(pick.coerceAtMost((days.size - 1).coerceAtLeast(0)))
            if (d == null) DayView(days, null, emptyList(), emptyList())
            else {
                val end = History.nextDay(d)
                DayView(days, history.summary(band, d, end), history.chart(band, d, end, 30),
                    history.events(band, d, end))
            }
        }
    }
    val v = view

    Column(
        Modifier.fillMaxSize().statusBarsPadding().verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        Row(Modifier.fillMaxWidth().padding(start = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("History", color = Pw.text, fontSize = 20.sp, fontWeight = FontWeight.SemiBold)
                Text("$band · kept on this phone for 30 days", color = Pw.dim, fontSize = 13.sp)
            }
            IconButton(onClick = onExport, enabled = (v?.days?.isNotEmpty() == true)) {
                Icon(Icons.Filled.Share, "Export", tint = if (v?.days?.isNotEmpty() == true) Pw.calm else Pw.faint)
            }
        }

        if (v == null) return@Column
        if (v.days.isEmpty()) {
            Panel {
                Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally) {
                    Spacer(Modifier.height(18.dp))
                    SearchRings(Modifier.size(90.dp))
                    Spacer(Modifier.height(14.dp))
                    Text("Nothing recorded yet", color = Pw.text, fontSize = 17.sp, fontWeight = FontWeight.SemiBold)
                    Spacer(Modifier.height(6.dp))
                    Text("Every second the band reads your pulse is kept here: heart rate, stress level and " +
                            "each flag, by day.", color = Pw.dim, fontSize = 14.sp, lineHeight = 20.sp,
                        textAlign = androidx.compose.ui.text.style.TextAlign.Center)
                    Spacer(Modifier.height(18.dp))
                }
            }
            return@Column
        }

        // ---- days
        val dayFmt = remember { SimpleDateFormat("EEE d MMM", Locale.getDefault()) }
        val today = History.dayStart(System.currentTimeMillis())
        Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            v.days.forEachIndexed { i, d ->
                val label = when (d) {
                    today -> "Today"
                    History.dayStart(today - 1) -> "Yesterday"
                    else -> dayFmt.format(Date(d))
                }
                val on = i == pick
                Box(
                    Modifier.clip(RoundedCornerShape(50))
                        .background(if (on) Pw.calm else Pw.panel)
                        .clickable { pick = i }
                        .padding(horizontal = 16.dp, vertical = 9.dp)
                ) { Text(label, color = if (on) Pw.ink else Pw.dim, fontSize = 13.sp, fontWeight = FontWeight.Medium) }
            }
        }

        // ---- the day in four numbers
        val s = v.summary
        if (s != null) {
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Stat(Modifier.weight(1f), "Monitored", duration(s.monitoredS.toLong()))
                Stat(Modifier.weight(1f), "Calm", s.calmPct?.let { "${it.toInt()}%" } ?: "–",
                    tone = if ((s.calmPct ?: 100f) >= 90f) Pw.calm else Pw.near)
            }
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Stat(Modifier.weight(1f), "Flags", "${s.events}", tone = if (s.events > 0) Pw.alert else Pw.text)
                Stat(Modifier.weight(1f), "Heart rate", s.avgHr?.let { "${it.toInt()}" } ?: "–",
                    unit = if (s.avgHr != null) "bpm avg" else "",
                    foot = if (s.minHr != null) "${s.minHr}–${s.maxHr} bpm" else null)
            }
        }

        // ---- the day as a chart
        Panel {
            Column {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                    Eyebrow("Through the day")
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Box(Modifier.size(8.dp).clip(CircleShape).background(Pw.calm))
                        Text(" stress  ", color = Pw.faint, fontSize = 11.sp)
                        Box(Modifier.size(8.dp).clip(CircleShape).background(Pw.heart))
                        Text(" heart rate", color = Pw.faint, fontSize = 11.sp)
                    }
                }
                Spacer(Modifier.height(12.dp))
                DayChart(v.chart, Modifier.fillMaxWidth().height(170.dp))
            }
        }

        // ---- every flag that day
        Eyebrow("Flags", Pw.dim)
        if (v.events.isEmpty()) {
            Panel {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(Modifier.size(10.dp).clip(CircleShape).background(Pw.calm))
                    Spacer(Modifier.width(12.dp))
                    Text("No flags this day.", color = Pw.dim, fontSize = 14.sp)
                }
            }
        } else {
            val hm = remember { SimpleDateFormat("HH:mm", Locale.getDefault()) }
            v.events.forEach { e -> EventCard(e, hm) }
        }
        Spacer(Modifier.height(4.dp))
    }
}

@Composable
private fun Stat(modifier: Modifier, label: String, value: String, unit: String = "", tone: Color = Pw.text,
                 foot: String? = null) {
    Box(modifier) {
        Panel {
            Column {
                Eyebrow(label)
                Spacer(Modifier.height(8.dp))
                Row(verticalAlignment = Alignment.Bottom) {
                    Text(value, color = tone, fontSize = 26.sp, fontWeight = FontWeight.SemiBold)
                    if (unit.isNotEmpty()) {
                        Spacer(Modifier.width(5.dp))
                        Text(unit, color = Pw.dim, fontSize = 12.sp, modifier = Modifier.padding(bottom = 4.dp))
                    }
                }
                if (foot != null) Text(foot, color = Pw.faint, fontSize = 12.sp)
            }
        }
    }
}

@Composable
private fun EventCard(e: FlagEvent, hm: SimpleDateFormat) {
    Panel {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(44.dp).clip(CircleShape).background(Pw.alert.copy(alpha = 0.14f)),
                contentAlignment = Alignment.Center) {
                Text("${(e.peakLevel * 100).toInt()}", color = Pw.alert, fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
            }
            Spacer(Modifier.width(14.dp))
            Column(Modifier.weight(1f)) {
                Text(hm.format(Date(e.start)) + " – " + (e.end?.let { hm.format(Date(it)) } ?: "now"),
                    color = Pw.text, fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
                Text("${duration(e.durationS)} above the line · peak ${(e.peakLevel * 100).toInt()}%" +
                        (e.peakHr?.let { " · HR up to $it" } ?: ""), color = Pw.dim, fontSize = 13.sp)
                Text(if (e.source == "master") "Judged by the master" else "Judged on the band",
                    color = Pw.faint, fontSize = 12.sp)
            }
        }
    }
}

fun duration(s: Long): String = when {
    s >= 3600 -> "${s / 3600} h ${(s % 3600) / 60} min"
    s >= 60 -> "${s / 60} min"
    else -> "$s s"
}

/** stress level as an area (coral where flagged) and heart rate as a line, over the day's readings */
@Composable
fun DayChart(pts: List<ChartPoint>, modifier: Modifier) {
    val label = remember {
        android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG).apply {
            color = android.graphics.Color.argb(255, 0x5A, 0x64, 0x72)
        }
    }
    val hm = remember { SimpleDateFormat("HH:mm", Locale.getDefault()) }
    Canvas(modifier) {
        val left = 36.dp.toPx()
        val right = size.width - 34.dp.toPx()
        val top = 6.dp.toPx()
        val bottom = size.height - 18.dp.toPx()
        label.textSize = 10.dp.toPx()
        val dash = PathEffect.dashPathEffect(floatArrayOf(6f, 6f))
        for (k in 0..2) {
            val y = top + (bottom - top) * k / 2f
            drawLine(Pw.line, Offset(left, y), Offset(right, y), strokeWidth = 1f, pathEffect = dash)
        }
        drawIntoCanvas { c ->
            label.textAlign = android.graphics.Paint.Align.RIGHT
            c.nativeCanvas.drawText("100%", left - 6.dp.toPx(), top + 4.dp.toPx(), label)
            c.nativeCanvas.drawText("0%", left - 6.dp.toPx(), bottom + 3.dp.toPx(), label)
        }
        if (pts.size < 2) return@Canvas
        // epoch ms don't fit a Float: work in ms since the first point
        val t0 = pts.first().t
        val span = maxOf(pts.last().t - t0, 60_000L).toFloat()
        fun x(t: Long) = left + (t - t0).toFloat() / span * (right - left)
        fun yl(v: Float) = bottom - v.coerceIn(0f, 1f) * (bottom - top)
        for (i in 1 until pts.size) {
            val a = pts[i - 1]; val b = pts[i]
            if (a.level == null || b.level == null || b.t - a.t > 5 * 60_000) continue
            val flagged = a.flagged || b.flagged
            val area = Path().apply {
                moveTo(x(a.t), bottom); lineTo(x(a.t), yl(a.level)); lineTo(x(b.t), yl(b.level)); lineTo(x(b.t), bottom); close()
            }
            val col = if (flagged) Pw.alert else Pw.calm
            drawPath(area, Brush.verticalGradient(listOf(col.copy(alpha = 0.55f), col.copy(alpha = 0.08f)),
                startY = top, endY = bottom))
        }
        val hrs = pts.mapNotNull { it.hr }
        if (hrs.size >= 2) {
            val lo = (hrs.min() - 5f).coerceAtLeast(30f)
            val hi = maxOf(hrs.max() + 5f, lo + 20f)
            fun yh(v: Float) = bottom - (v - lo) / (hi - lo) * (bottom - top)
            val line = Path()
            var pen = false
            var last: ChartPoint? = null
            for (p in pts) {
                val v = p.hr
                if (v == null || (last != null && p.t - last.t > 5 * 60_000)) pen = false
                if (v != null) { if (pen) line.lineTo(x(p.t), yh(v)) else line.moveTo(x(p.t), yh(v)); pen = true }
                last = p
            }
            drawPath(line, Pw.heart, style = Stroke(2.dp.toPx(), join = StrokeJoin.Round))
            drawIntoCanvas { c ->
                label.textAlign = android.graphics.Paint.Align.LEFT
                label.color = android.graphics.Color.argb(255, 0xFF, 0x5A, 0x6E)
                c.nativeCanvas.drawText("${hi.toInt()}", right + 6.dp.toPx(), top + 4.dp.toPx(), label)
                c.nativeCanvas.drawText("${lo.toInt()}", right + 6.dp.toPx(), bottom + 3.dp.toPx(), label)
                label.color = android.graphics.Color.argb(255, 0x5A, 0x64, 0x72)
            }
        }
        drawIntoCanvas { c ->
            label.textAlign = android.graphics.Paint.Align.LEFT
            c.nativeCanvas.drawText(hm.format(Date(pts.first().t)), left, size.height - 2.dp.toPx(), label)
            label.textAlign = android.graphics.Paint.Align.RIGHT
            c.nativeCanvas.drawText(hm.format(Date(pts.last().t)), right, size.height - 2.dp.toPx(), label)
        }
    }
}
