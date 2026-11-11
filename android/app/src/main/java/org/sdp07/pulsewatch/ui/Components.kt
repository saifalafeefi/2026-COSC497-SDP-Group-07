package org.sdp07.pulsewatch.ui

import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
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
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.drawIntoCanvas
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import org.sdp07.pulsewatch.WaveBuffer
import kotlin.math.max
import kotlin.math.min

/** a quiet panel; the one container the dashboard is built from */
@Composable
fun Panel(modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    Box(
        modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(20.dp))
            .background(Pw.panel)
            .border(1.dp, Pw.line.copy(alpha = 0.6f), RoundedCornerShape(20.dp))
            .padding(18.dp)
    ) { content() }
}

@Composable
fun Eyebrow(text: String, color: Color = Pw.dim) =
    Text(text.uppercase(), color = color, fontSize = 11.sp, fontWeight = FontWeight.Medium,
        letterSpacing = 1.2.sp)

@Composable
fun Pill(text: String, color: Color, pulsing: Boolean = false) {
    val t = rememberInfiniteTransition(label = "pill")
    val a by t.animateFloat(0.35f, 1f, infiniteRepeatable(tween(900), RepeatMode.Reverse), label = "a")
    Row(
        Modifier
            .clip(RoundedCornerShape(50))
            .background(color.copy(alpha = 0.14f))
            .padding(horizontal = 12.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Box(Modifier.size(8.dp).clip(CircleShape).background(color.copy(alpha = if (pulsing) a else 1f)))
        Spacer(Modifier.width(8.dp))
        Text(text, color = color, fontSize = 12.sp, fontWeight = FontWeight.Medium)
    }
}

/**
 * The stress level against the wearer's own line: a 240-degree arc, the line
 * as a tick, the fill colour going from calm to amber near the line to coral
 * once the band has actually flagged.
 */
@Composable
fun LevelGauge(level: Float?, threshold: Float, flagged: Boolean, size: Dp = 220.dp,
               center: @Composable () -> Unit) {
    val target = (level ?: 0f).coerceIn(0f, 1f)
    val v by animateFloatAsState(target, tween(700, easing = FastOutSlowInEasing), label = "lvl")
    val col by animateColorAsState(
        when {
            level == null -> Pw.faint
            flagged -> Pw.alert
            target >= threshold * 0.85f -> Pw.near
            else -> Pw.calm
        }, tween(500), label = "col")
    Box(Modifier.size(size), contentAlignment = Alignment.Center) {
        Canvas(Modifier.size(size)) {
            val stroke = 16.dp.toPx()
            val inset = stroke / 2 + 4.dp.toPx()
            val arcSize = Size(this.size.width - 2 * inset, this.size.height - 2 * inset)
            val tl = Offset(inset, inset)
            val start = 150f
            val sweep = 240f
            drawArc(Pw.panelHi, start, sweep, false, tl, arcSize, style = Stroke(stroke, cap = StrokeCap.Round))
            if (level != null && v > 0.002f) {
                drawArc(
                    Brush.sweepGradient(listOf(col.copy(alpha = 0.55f), col, col.copy(alpha = 0.55f))),
                    start, sweep * v, false, tl, arcSize, style = Stroke(stroke, cap = StrokeCap.Round))
            }
            // the wearer's line
            val ang = Math.toRadians((start + sweep * threshold.coerceIn(0f, 1f)).toDouble())
            val r1 = arcSize.width / 2 - stroke * 0.9f
            val r2 = arcSize.width / 2 + stroke * 0.9f
            val c = Offset(this.size.width / 2, this.size.height / 2)
            drawLine(Pw.text.copy(alpha = 0.85f),
                Offset(c.x + r1 * Math.cos(ang).toFloat(), c.y + r1 * Math.sin(ang).toFloat()),
                Offset(c.x + r2 * Math.cos(ang).toFloat(), c.y + r2 * Math.sin(ang).toFloat()),
                strokeWidth = 3.dp.toPx(), cap = StrokeCap.Round)
        }
        center()
    }
}

/** holds the waveform view's scroll and scale between frames, outside Compose state */
private class WaveView {
    var shown = -1.0
    var lo = -400f
    var hi = 400f
    var lastNs = 0L
    val buf = FloatArray(64 * 6)
}

/**
 * The live pulse, scrolling smoothly at 64 samples a second even though the
 * band sends them in bursts of ~8: the view trails the newest sample by a
 * quarter second and glides toward it. Sized from the last 3 s, eased.
 */
@Composable
fun PulseWave(wave: WaveBuffer, active: Boolean, modifier: Modifier = Modifier,
              color: Color = Pw.calm) {
    val view = remember { WaveView() }
    var frame by remember { mutableLongStateOf(0L) }
    LaunchedEffect(wave) {
        while (true) {
            withFrameNanos { t ->
                val dt = if (view.lastNs == 0L) 0.0 else (t - view.lastNs) / 1e9
                view.lastNs = t
                val end = wave.end
                if (end > 0) {
                    val target = end.toDouble() - 16.0          // trail by 0.25 s
                    view.shown = if (view.shown < 0 || target - view.shown > 64 || view.shown > target + 8)
                        target else min(target, view.shown + dt * 64.0 * (if (target - view.shown > 24) 1.25 else 1.0))
                    val right = view.shown.toLong()
                    val n = view.buf.size
                    wave.copy(right - n, right, view.buf)
                    var mn = Float.MAX_VALUE
                    var mx = -Float.MAX_VALUE
                    for (i in n - 192 until n) {
                        val x = view.buf[i]
                        if (!x.isNaN()) { mn = min(mn, x); mx = max(mx, x) }
                    }
                    if (mn <= mx) {
                        val pad = max(60f, (mx - mn) * 0.12f)
                        view.lo += ((mn - pad) - view.lo) * 0.08f
                        view.hi += ((mx + pad) - view.hi) * 0.08f
                    }
                }
                frame = t
            }
        }
    }
    val labelPaint = remember {
        android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG).apply {
            this.color = android.graphics.Color.argb(255, 0x5A, 0x64, 0x72)
            textAlign = android.graphics.Paint.Align.RIGHT
        }
    }
    Canvas(modifier) {
        if (frame < 0L) return@Canvas                 // reading it redraws every frame
        val gutter = 40.dp.toPx()                     // the y-axis labels live here
        val w = size.width - gutter
        val h = size.height
        labelPaint.textSize = 10.dp.toPx()
        if (!active || wave.end <= 0) {
            drawLine(Pw.line.copy(alpha = 0.5f), Offset(gutter, h / 2), Offset(size.width, h / 2), strokeWidth = 1f)
            drawLine(Pw.faint, Offset(gutter, h / 2), Offset(size.width, h / 2), strokeWidth = 2.dp.toPx())
            drawIntoCanvas { it.nativeCanvas.drawText("0", gutter - 6.dp.toPx(), h / 2 + 3.5f.dp.toPx(), labelPaint) }
            return@Canvas
        }
        val span = max(1f, view.hi - view.lo)
        fun yOf(v: Float) = h - (v.coerceIn(view.lo, view.hi) - view.lo) / span * h
        // round ticks, 1-2-5 steps, about three of them
        val raw = span / 3f
        val mag = Math.pow(10.0, Math.floor(Math.log10(raw.toDouble()))).toFloat()
        val step = listOf(1f, 2f, 5f, 10f).map { it * mag }.first { it >= raw }
        var t = Math.ceil((view.lo / step).toDouble()).toFloat() * step
        while (t <= view.hi) {
            val y = yOf(t)
            if (y > 6.dp.toPx() && y < h - 2.dp.toPx()) {
                drawLine(Pw.line.copy(alpha = if (t == 0f) 0.9f else 0.45f), Offset(gutter, y), Offset(size.width, y),
                    strokeWidth = 1f)
                val txt = if (t > 0f) "+" + t.toInt() else t.toInt().toString()
                drawIntoCanvas { it.nativeCanvas.drawText(txt, gutter - 6.dp.toPx(), y + 3.5f.dp.toPx(), labelPaint) }
            }
            t += step
        }
        val n = view.buf.size
        val p = Path()
        var pen = false
        for (i in 0 until n) {
            val x = view.buf[i]
            if (x.isNaN()) { pen = false; continue }
            val px = gutter + w * i / (n - 1).toFloat()
            val py = yOf(x)
            if (pen) p.lineTo(px, py) else p.moveTo(px, py)
            pen = true
        }
        drawPath(p, Brush.horizontalGradient(listOf(color.copy(alpha = 0.15f), color, color),
            startX = gutter, endX = size.width),
            style = Stroke(2.5.dp.toPx(), cap = StrokeCap.Round, join = StrokeJoin.Round))
    }
}

/** heart rate over the last ten minutes */
@Composable
fun HrTrend(points: List<Pair<Long, Int>>, now: Long, modifier: Modifier = Modifier) {
    Canvas(modifier) {
        val w = size.width
        val h = size.height
        drawLine(Pw.line, Offset(0f, h - 1), Offset(w, h - 1), strokeWidth = 1f)
        if (points.size < 2) return@Canvas
        val lo = (points.minOf { it.second } - 5).toFloat()
        val hi = (points.maxOf { it.second } + 5).toFloat()
        val span = max(10f, hi - lo)
        val win = 10 * 60_000f
        val p = Path()
        val fill = Path()
        points.forEachIndexed { i, (t, bpm) ->
            val x = w * (1f - (now - t) / win)
            val y = h - (bpm - lo) / span * h
            if (i == 0) { p.moveTo(x, y); fill.moveTo(x, h); fill.lineTo(x, y) } else { p.lineTo(x, y); fill.lineTo(x, y) }
        }
        val lastX = w * (1f - (now - points.last().first) / win)
        fill.lineTo(lastX, h); fill.close()
        drawPath(fill, Brush.verticalGradient(listOf(Pw.heart.copy(alpha = 0.28f), Color.Transparent)))
        drawPath(p, Pw.heart, style = Stroke(2.dp.toPx(), cap = StrokeCap.Round, join = StrokeJoin.Round))
        val (t, bpm) = points.last()
        drawCircle(Pw.heart, 4.dp.toPx(), Offset(w * (1f - (now - t) / win), h - (bpm - lo) / span * h))
    }
}

/** a thin labelled bar: a level against its own line, used for the band's two detectors */
@Composable
fun LevelBar(label: String, detail: String, level: Float?, threshold: Float, flagged: Boolean) {
    val v by animateFloatAsState((level ?: 0f).coerceIn(0f, 1f), tween(600, easing = LinearEasing), label = "bar")
    val col = when { level == null -> Pw.faint; flagged -> Pw.alert; v >= threshold * 0.85f -> Pw.near; else -> Pw.calm }
    Column(Modifier.fillMaxWidth()) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            Text(label, color = Pw.text, fontSize = 14.sp)
            Text(if (level == null) detail else "${(v * 100).toInt()}%  ·  $detail", color = Pw.dim, fontSize = 13.sp)
        }
        Spacer(Modifier.height(8.dp))
        Box(Modifier.fillMaxWidth().height(8.dp).clip(RoundedCornerShape(4.dp)).background(Pw.panelHi)) {
            if (level != null) Box(Modifier.fillMaxWidth(v).height(8.dp).clip(RoundedCornerShape(4.dp)).background(col))
        }
    }
}

/** the pulsing rings on the pairing screen */
@Composable
fun SearchRings(modifier: Modifier = Modifier) {
    val t = rememberInfiniteTransition(label = "rings")
    val p by t.animateFloat(0f, 1f, infiniteRepeatable(tween(2400, easing = LinearEasing)), label = "p")
    Canvas(modifier) {
        val c = Offset(size.width / 2, size.height / 2)
        val rMax = min(size.width, size.height) / 2
        for (k in 0 until 3) {
            val f = (p + k / 3f) % 1f
            drawCircle(Pw.calm.copy(alpha = (1f - f) * 0.45f), rMax * (0.25f + 0.75f * f), c,
                style = Stroke(2.dp.toPx()))
        }
        drawCircle(Pw.calm, rMax * 0.16f, c)
        drawCircle(Pw.ink, rMax * 0.09f, c)
    }
}
