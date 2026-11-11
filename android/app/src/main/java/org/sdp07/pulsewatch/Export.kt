package org.sdp07.pulsewatch

import android.content.ClipData
import android.content.Context
import android.content.Intent
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.DashPathEffect
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RectF
import android.graphics.Typeface
import android.graphics.pdf.PdfDocument
import android.net.Uri
import androidx.core.content.FileProvider
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * The phone's history, out of the phone: a designed PDF report (summary, a chart
 * per day, every flag) and the raw rows as CSV. Written to the app's cache and
 * handed to Android's share sheet; nothing is uploaded by the app itself.
 */
object Export {
    private const val W = 595f          // A4 in PDF points
    private const val H = 842f
    private const val M = 40f           // page margin

    private val ink = Color.rgb(14, 17, 22)
    private val text = Color.rgb(30, 36, 46)
    private val dim = Color.rgb(102, 112, 128)
    private val faint = Color.rgb(170, 178, 190)
    private val rule = Color.rgb(226, 230, 236)
    private val panel = Color.rgb(246, 248, 250)
    private val calm = Color.rgb(32, 168, 140)
    private val alert = Color.rgb(226, 82, 82)
    private val heart = Color.rgb(222, 72, 96)

    private fun dir(ctx: Context) = File(ctx.cacheDir, "exports").apply { mkdirs() }

    private fun uri(ctx: Context, f: File): Uri =
        FileProvider.getUriForFile(ctx, ctx.packageName + ".exports", f)

    fun share(ctx: Context, files: List<File>, mime: String, subject: String) {
        val uris = ArrayList(files.map { uri(ctx, it) })
        val send = if (uris.size == 1) Intent(Intent.ACTION_SEND).putExtra(Intent.EXTRA_STREAM, uris[0])
                   else Intent(Intent.ACTION_SEND_MULTIPLE).putParcelableArrayListExtra(Intent.EXTRA_STREAM, uris)
        send.type = mime
        send.putExtra(Intent.EXTRA_SUBJECT, subject)
        send.clipData = ClipData.newRawUri(subject, uris[0]).apply { uris.drop(1).forEach { addItem(ClipData.Item(it)) } }
        send.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        ctx.startActivity(Intent.createChooser(send, subject).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }

    // ------------------------------------------------------------------ CSV

    fun csv(ctx: Context, h: History, band: String, from: Long, to: Long): List<File> {
        val iso = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.US)
        val stamp = SimpleDateFormat("yyyyMMdd", Locale.US).format(Date())
        val safe = band.replace(Regex("[^A-Za-z0-9]+"), "-").trim('-')
        val r = File(dir(ctx), "pulse-$safe-readings-$stamp.csv")
        r.bufferedWriter().use { w ->
            w.write("time,heart_rate_bpm,spo2_pct,stress_level,flagged,state,judged_by,motion,band_detector_level,band_ai_level\n")
            h.readings(band, from, to) { x ->
                w.write(listOf(iso.format(Date(x.t)), x.hr ?: "", x.spo2 ?: "", x.level?.let { "%.3f".format(Locale.US, it) } ?: "",
                    if (x.flag) 1 else 0, x.state, x.source, x.motion?.let { "%.3f".format(Locale.US, it) } ?: "",
                    x.bandLevel?.let { "%.3f".format(Locale.US, it) } ?: "",
                    x.aiLevel?.let { "%.3f".format(Locale.US, it) } ?: "").joinToString(","))
                w.write("\n")
            }
        }
        val e = File(dir(ctx), "pulse-$safe-flags-$stamp.csv")
        e.bufferedWriter().use { w ->
            w.write("start,end,duration_s,peak_level,peak_heart_rate_bpm,average_heart_rate_bpm,judged_by\n")
            h.events(band, from, to).sortedBy { it.start }.forEach { x ->
                w.write(listOf(iso.format(Date(x.start)), x.end?.let { iso.format(Date(it)) } ?: "", x.durationS,
                    "%.3f".format(Locale.US, x.peakLevel), x.peakHr ?: "",
                    x.avgHr?.let { "%.1f".format(Locale.US, it) } ?: "", x.source).joinToString(","))
                w.write("\n")
            }
        }
        return listOf(r, e)
    }

    // ------------------------------------------------------------------ PDF

    private class Pager(val doc: PdfDocument, val band: String, val period: String) {
        var page: PdfDocument.Page? = null
        var n = 0
        var y = 0f
        val c: Canvas get() = page!!.canvas

        fun newPage() {
            page?.let { finishPage(it) }
            n++
            page = doc.startPage(PdfDocument.PageInfo.Builder(W.toInt(), H.toInt(), n).create())
            y = M
            if (n > 1) {                      // a slim running header after the cover
                val p = paint(9f, dim)
                c.drawText("Pulse Watch · $band · $period", M, y + 8, p)
                y += 26
            }
        }

        fun need(h: Float) { if (y + h > H - M - 24) newPage() }

        private fun finishPage(p: PdfDocument.Page) {
            val f = paint(8f, faint)
            p.canvas.drawText("An early-warning aid, not a medical device. Every flag is for a person to review.", M, H - 22, f)
            f.textAlign = Paint.Align.RIGHT
            p.canvas.drawText("Page $n", W - M, H - 22, f)
            doc.finishPage(p)
        }

        fun finish() { page?.let { finishPage(it) }; page = null }
    }

    private fun paint(size: Float, color: Int, bold: Boolean = false) = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        textSize = size; this.color = color
        typeface = Typeface.create(Typeface.SANS_SERIF, if (bold) Typeface.BOLD else Typeface.NORMAL)
    }

    private fun dur(s: Long): String = when {
        s >= 3600 -> "${s / 3600} h ${(s % 3600) / 60} min"
        s >= 60 -> "${s / 60} min ${s % 60} s"
        else -> "$s s"
    }

    fun pdf(ctx: Context, h: History, band: String, days: List<Long>): File {
        val dayFmt = SimpleDateFormat("EEE d MMM yyyy", Locale.getDefault())
        val short = SimpleDateFormat("d MMM", Locale.getDefault())
        val hm = SimpleDateFormat("HH:mm", Locale.getDefault())
        val sorted = days.sorted()
        val from = sorted.first()
        val to = History.nextDay(sorted.last())
        val period = if (sorted.size == 1) dayFmt.format(Date(from))
                     else short.format(Date(from)) + " – " + dayFmt.format(Date(sorted.last()))
        val doc = PdfDocument()
        val pg = Pager(doc, band, period)
        pg.newPage()
        val c0 = pg.c

        // ---- cover band
        val bandRect = RectF(0f, 0f, W, 150f)
        c0.drawRect(bandRect, Paint().apply { color = ink })
        c0.drawText("PULSE WATCH REPORT", M, 52f, paint(10f, Color.rgb(140, 150, 165), true).apply { letterSpacing = 0.18f })
        c0.drawText(band, M, 88f, paint(26f, Color.WHITE, true))
        c0.drawText(period, M, 112f, paint(12f, Color.rgb(190, 198, 210)))
        c0.drawText("Generated " + SimpleDateFormat("d MMM yyyy, HH:mm", Locale.getDefault()).format(Date()),
            M, 132f, paint(9f, Color.rgb(140, 150, 165)))
        // the trace motif from the app icon
        val trace = Path().apply {
            moveTo(W - 190f, 92f); lineTo(W - 150f, 92f); lineTo(W - 136f, 62f); lineTo(W - 118f, 118f)
            lineTo(W - 100f, 48f); lineTo(W - 86f, 92f); lineTo(W - M, 92f)
        }
        c0.drawPath(trace, Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = Color.rgb(79, 209, 181); style = Paint.Style.STROKE; strokeWidth = 3.5f
            strokeCap = Paint.Cap.ROUND; strokeJoin = Paint.Join.ROUND
        })
        pg.y = 180f

        // ---- summary over the whole period
        val all = h.summary(band, from, to)
        val tiles = listOf(
            "Monitored" to dur(all.monitoredS.toLong()),
            "Calm" to (all.calmPct?.let { "${it.toInt()}%" } ?: "–"),
            "Flags" to "${all.events}",
            "Average heart rate" to (all.avgHr?.let { "${it.toInt()} bpm" } ?: "–"),
        )
        val tw = (W - 2 * M - 3 * 10f) / 4
        tiles.forEachIndexed { i, (k, v) ->
            val x = M + i * (tw + 10f)
            pg.c.drawRoundRect(RectF(x, pg.y, x + tw, pg.y + 64f), 10f, 10f, Paint().apply { color = panel })
            pg.c.drawText(k.uppercase(), x + 12f, pg.y + 22f, paint(7.5f, dim, true).apply { letterSpacing = 0.12f })
            pg.c.drawText(v, x + 12f, pg.y + 48f, paint(17f, text, true))
        }
        pg.y += 64f + 14f
        val range = if (all.minHr != null) "Heart rate ranged ${all.minHr}–${all.maxHr} bpm" else "No heart rate read"
        val sp = all.avgSpo2?.let { ", SpO₂ averaged ${it.toInt()}%" } ?: ""
        pg.c.drawText("$range$sp. Stress level is the band's own read against your recent normal; " +
            "a flag means it stayed above your line.", M, pg.y + 10f, paint(9f, dim))
        pg.y += 34f

        // ---- one chart per day
        for (d in sorted.reversed()) {
            val s = h.summary(band, d, History.nextDay(d))
            if (s.monitoredS == 0) continue
            val pts = h.chart(band, d, History.nextDay(d), 60)
            pg.need(200f)
            val c = pg.c
            c.drawText(dayFmt.format(Date(d)), M, pg.y + 14f, paint(13f, text, true))
            val stats = "${dur(s.monitoredS.toLong())} monitored · " +
                (s.calmPct?.let { "${it.toInt()}% calm · " } ?: "") + "${s.events} flag${if (s.events == 1) "" else "s"}" +
                (s.avgHr?.let { " · avg ${it.toInt()} bpm" } ?: "")
            c.drawText(stats, W - M, pg.y + 14f, paint(9f, dim).apply { textAlign = Paint.Align.RIGHT })
            pg.y += 26f
            drawDayChart(c, RectF(M, pg.y, W - M, pg.y + 130f), pts, hm)
            pg.y += 130f + 30f
        }

        // ---- every flag
        val events = h.events(band, from, to).sortedBy { it.start }
        pg.need(80f)
        pg.c.drawText("Flags", M, pg.y + 14f, paint(13f, text, true))
        pg.y += 28f
        if (events.isEmpty()) {
            pg.c.drawText("No flags in this period.", M, pg.y + 10f, paint(10f, dim))
            pg.y += 24f
        } else {
            val cols = floatArrayOf(M, M + 120f, M + 200f, M + 290f, M + 370f, M + 450f)
            val heads = listOf("When", "Lasted", "Peak level", "Peak HR", "Avg HR", "Judged by")
            heads.forEachIndexed { i, t -> pg.c.drawText(t.uppercase(), cols[i], pg.y + 8f, paint(7.5f, dim, true).apply { letterSpacing = 0.1f }) }
            pg.y += 16f
            val when_ = SimpleDateFormat("d MMM, HH:mm", Locale.getDefault())
            for (e in events) {
                pg.need(22f)
                val c = pg.c
                c.drawLine(M, pg.y, W - M, pg.y, Paint().apply { color = rule; strokeWidth = 0.8f })
                val row = listOf(when_.format(Date(e.start)), dur(e.durationS), "${(e.peakLevel * 100).toInt()}%",
                    e.peakHr?.let { "$it bpm" } ?: "–", e.avgHr?.let { "${it.toInt()} bpm" } ?: "–",
                    if (e.source == "master") "the master" else "the band")
                row.forEachIndexed { i, t ->
                    c.drawText(t, cols[i], pg.y + 15f, paint(9.5f, if (i == 2) alert else text, i == 2))
                }
                pg.y += 22f
            }
        }
        pg.finish()

        val safe = band.replace(Regex("[^A-Za-z0-9]+"), "-").trim('-')
        val f = File(dir(ctx), "pulse-$safe-report-" + SimpleDateFormat("yyyyMMdd", Locale.US).format(Date()) + ".pdf")
        f.outputStream().use { doc.writeTo(it) }
        doc.close()
        return f
    }

    /** a day: stress level as a filled area (coral where flagged), heart rate as a line */
    private fun drawDayChart(c: Canvas, r: RectF, pts: List<ChartPoint>, hm: SimpleDateFormat) {
        c.drawRoundRect(r, 10f, 10f, Paint().apply { color = panel })
        if (pts.size < 2) return
        val inner = RectF(r.left + 34f, r.top + 12f, r.right - 34f, r.bottom - 22f)
        val t0 = pts.first().t           // epoch ms don't fit a Float
        val span = maxOf(pts.last().t - t0, 60_000L).toFloat()
        fun x(t: Long) = inner.left + (t - t0).toFloat() / span * inner.width()
        val grid = Paint().apply { color = rule; strokeWidth = 0.7f; pathEffect = DashPathEffect(floatArrayOf(3f, 3f), 0f) }
        for (k in 0..2) { val y = inner.top + inner.height() * k / 2f; c.drawLine(inner.left, y, inner.right, y, grid) }
        // level 0..1
        fun yl(v: Float) = inner.bottom - v.coerceIn(0f, 1f) * inner.height()
        var prev: ChartPoint? = null
        for (p in pts) {
            val q = prev
            if (q != null && p.level != null && q.level != null && p.t - q.t < 5 * 60_000) {
                val area = Path().apply {
                    moveTo(x(q.t), inner.bottom); lineTo(x(q.t), yl(q.level)); lineTo(x(p.t), yl(p.level)); lineTo(x(p.t), inner.bottom); close()
                }
                c.drawPath(area, Paint(Paint.ANTI_ALIAS_FLAG).apply {
                    color = if (p.flagged || q.flagged) alert else calm; alpha = if (p.flagged || q.flagged) 110 else 70
                })
            }
            prev = p
        }
        // heart rate on its own scale
        val hrs = pts.mapNotNull { it.hr }
        if (hrs.size >= 2) {
            val lo = (hrs.min() - 5).coerceAtLeast(30f)
            val hi = maxOf(hrs.max() + 5, lo + 20f)
            fun yh(v: Float) = inner.bottom - (v - lo) / (hi - lo) * inner.height()
            val line = Path()
            var pen = false
            var last: ChartPoint? = null
            for (p in pts) {
                val v = p.hr
                if (v == null || (last != null && p.t - last.t > 5 * 60_000)) pen = false
                if (v != null) { if (pen) line.lineTo(x(p.t), yh(v)) else line.moveTo(x(p.t), yh(v)); pen = true }
                last = p
            }
            c.drawPath(line, Paint(Paint.ANTI_ALIAS_FLAG).apply {
                color = heart; style = Paint.Style.STROKE; strokeWidth = 1.6f; strokeJoin = Paint.Join.ROUND
            })
            val lp = paint(7.5f, heart).apply { textAlign = Paint.Align.LEFT }
            c.drawText("${hi.toInt()} bpm", inner.right + 4f, inner.top + 6f, lp)
            c.drawText("${lo.toInt()} bpm", inner.right + 4f, inner.bottom, lp)
        }
        val lab = paint(7.5f, dim).apply { textAlign = Paint.Align.RIGHT }
        c.drawText("100%", inner.left - 4f, inner.top + 6f, lab)
        c.drawText("0%", inner.left - 4f, inner.bottom, lab)
        val tl = paint(7.5f, dim)
        c.drawText(hm.format(Date(pts.first().t)), inner.left, r.bottom - 8f, tl)
        tl.textAlign = Paint.Align.RIGHT
        c.drawText(hm.format(Date(pts.last().t)), inner.right, r.bottom - 8f, tl)
        tl.textAlign = Paint.Align.CENTER
        c.drawText("stress level (area) · heart rate (line)", r.centerX(), r.bottom - 8f, tl)
    }
}
