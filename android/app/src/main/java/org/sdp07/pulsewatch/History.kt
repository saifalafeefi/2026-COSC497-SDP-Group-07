package org.sdp07.pulsewatch

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import java.util.Calendar
import java.util.concurrent.Executors

/** one second the band was read with a finger on */
data class Reading(
    val t: Long, val hr: Int?, val spo2: Int?, val level: Float?, val flag: Boolean,
    val state: String, val source: String, val motion: Float?, val bandLevel: Float?, val aiLevel: Float?,
)

/** one flag, from the moment it rose to the moment it cleared */
data class FlagEvent(
    val id: Long, val start: Long, val end: Long?, val peakLevel: Float, val peakHr: Int?,
    val avgHr: Float?, val source: String,
) {
    val durationS: Long get() = ((end ?: System.currentTimeMillis()) - start) / 1000
}

data class DaySummary(
    val dayStart: Long, val monitoredS: Int, val calmPct: Float?, val flaggedS: Int, val events: Int,
    val avgHr: Float?, val minHr: Int?, val maxHr: Int?, val avgSpo2: Float?,
)

/** a point on a day's chart: a 30 s bucket's average */
data class ChartPoint(val t: Long, val hr: Float?, val level: Float?, val flagged: Boolean)

/**
 * Everything the app remembers, on the phone only: one row per second while the
 * band reads a pulse, and the flag episodes. Nothing leaves the phone unless the
 * user exports it. Kept 30 days. Writes go through one background thread.
 */
class History(ctx: Context) : SQLiteOpenHelper(ctx, "history.db", null, 1) {
    private val io = Executors.newSingleThreadExecutor()
    private var openEvent: Long? = null
    private var evPeak = 0f
    private var evPeakHr: Int? = null
    private var evHrSum = 0.0
    private var evHrN = 0
    private val _version = MutableStateFlow(0L)
    /** bumps whenever something new is stored, so screens can refresh */
    val version: StateFlow<Long> = _version
    private var lastBump = 0L

    override fun onCreate(db: SQLiteDatabase) {
        db.execSQL("""CREATE TABLE reading (t INTEGER NOT NULL, band TEXT NOT NULL, hr INTEGER, spo2 INTEGER,
            level REAL, flag INTEGER NOT NULL, state TEXT, src TEXT, motion REAL, band_level REAL, ai_level REAL)""")
        db.execSQL("CREATE INDEX reading_band_t ON reading(band, t)")
        db.execSQL("""CREATE TABLE event (id INTEGER PRIMARY KEY AUTOINCREMENT, band TEXT NOT NULL,
            start INTEGER NOT NULL, ending INTEGER, peak_level REAL, peak_hr INTEGER, avg_hr REAL, src TEXT)""")
        db.execSQL("CREATE INDEX event_band_start ON event(band, start)")
    }

    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) {}

    /** called once a second with the band's status, from any thread */
    fun record(band: String, s: BandStatus) {
        if (s.finger != "on") {               // no finger: close any open episode, store nothing
            if (openEvent != null) io.execute { closeEvent(System.currentTimeMillis()) }
            return
        }
        val t = System.currentTimeMillis()
        io.execute {
            val db = writableDatabase
            db.insert("reading", null, ContentValues().apply {
                put("t", t); put("band", band); put("hr", s.hr); put("spo2", s.spo2)
                put("level", s.level?.toDouble()); put("flag", if (s.flag) 1 else 0); put("state", s.state)
                put("src", s.source); put("motion", s.motion?.toDouble())
                put("band_level", s.bandLevel?.toDouble()); put("ai_level", s.aiLevel?.toDouble())
            })
            if (s.flag) {
                if (openEvent == null) {
                    openEvent = db.insert("event", null, ContentValues().apply {
                        put("band", band); put("start", t); put("src", s.source)
                    })
                    evPeak = 0f; evPeakHr = null; evHrSum = 0.0; evHrN = 0
                }
                evPeak = maxOf(evPeak, s.level ?: 0f)
                if (s.hr != null) {
                    evPeakHr = maxOf(evPeakHr ?: 0, s.hr); evHrSum += s.hr; evHrN++
                }
                db.update("event", ContentValues().apply {
                    put("peak_level", evPeak.toDouble()); put("peak_hr", evPeakHr)
                    put("avg_hr", if (evHrN > 0) evHrSum / evHrN else null)
                }, "id = ?", arrayOf(openEvent.toString()))
            } else if (openEvent != null) {
                closeEvent(t)
            }
            if (t - lastBump > 5000 || s.flag) { lastBump = t; _version.value = t }
        }
    }

    /** the link dropped or the finger came off: an open episode ends here */
    fun interrupt() = io.execute { closeEvent(System.currentTimeMillis()) }

    private fun closeEvent(t: Long) {
        val id = openEvent ?: return
        writableDatabase.update("event", ContentValues().apply { put("ending", t) }, "id = ?", arrayOf(id.toString()))
        openEvent = null
        _version.value = t
    }

    // ------------------------------------------------------------------ reads

    /** the local calendar days with readings, newest first */
    fun days(band: String, max: Int = 30): List<Long> {
        val out = ArrayList<Long>()
        readableDatabase.rawQuery(
            """SELECT MIN(t) FROM reading WHERE band = ?
               GROUP BY strftime('%Y-%m-%d', t / 1000, 'unixepoch', 'localtime') ORDER BY MIN(t) DESC LIMIT ?""",
            arrayOf(band, max.toString())).use { c -> while (c.moveToNext()) out += dayStart(c.getLong(0)) }
        return out
    }

    fun summary(band: String, from: Long, to: Long): DaySummary {
        readableDatabase.rawQuery(
            """SELECT COUNT(*), SUM(flag), SUM(CASE WHEN level IS NOT NULL AND flag = 0 THEN 1 ELSE 0 END),
                      SUM(CASE WHEN level IS NOT NULL THEN 1 ELSE 0 END), AVG(hr), MIN(hr), MAX(hr), AVG(spo2)
               FROM reading WHERE band = ? AND t >= ? AND t < ?""",
            arrayOf(band, from.toString(), to.toString())).use { c ->
            c.moveToFirst()
            val n = c.getInt(0)
            val scored = c.getInt(3)
            val ev = readableDatabase.rawQuery("SELECT COUNT(*) FROM event WHERE band = ? AND start >= ? AND start < ?",
                arrayOf(band, from.toString(), to.toString())).use { e -> e.moveToFirst(); e.getInt(0) }
            return DaySummary(
                from, n, if (scored > 0) c.getInt(2) * 100f / scored else null, c.getInt(1), ev,
                if (c.isNull(4)) null else c.getFloat(4), if (c.isNull(5)) null else c.getInt(5),
                if (c.isNull(6)) null else c.getInt(6), if (c.isNull(7)) null else c.getFloat(7))
        }
    }

    fun chart(band: String, from: Long, to: Long, bucketS: Int = 30): List<ChartPoint> {
        val out = ArrayList<ChartPoint>()
        val b = bucketS * 1000L
        readableDatabase.rawQuery(
            """SELECT (t / $b) * $b AS k, AVG(hr), AVG(level), MAX(flag) FROM reading
               WHERE band = ? AND t >= ? AND t < ? GROUP BY k ORDER BY k""",
            arrayOf(band, from.toString(), to.toString())).use { c ->
            while (c.moveToNext()) out += ChartPoint(c.getLong(0),
                if (c.isNull(1)) null else c.getFloat(1), if (c.isNull(2)) null else c.getFloat(2), c.getInt(3) == 1)
        }
        return out
    }

    fun events(band: String, from: Long, to: Long): List<FlagEvent> {
        val out = ArrayList<FlagEvent>()
        readableDatabase.rawQuery(
            "SELECT id, start, ending, peak_level, peak_hr, avg_hr, src FROM event WHERE band = ? AND start >= ? AND start < ? ORDER BY start DESC",
            arrayOf(band, from.toString(), to.toString())).use { c ->
            while (c.moveToNext()) out += FlagEvent(c.getLong(0), c.getLong(1), if (c.isNull(2)) null else c.getLong(2),
                c.getFloat(3), if (c.isNull(4)) null else c.getInt(4), if (c.isNull(5)) null else c.getFloat(5),
                c.getString(6) ?: "board")
        }
        return out
    }

    fun readings(band: String, from: Long, to: Long, each: (Reading) -> Unit) {
        readableDatabase.rawQuery(
            "SELECT t, hr, spo2, level, flag, state, src, motion, band_level, ai_level FROM reading WHERE band = ? AND t >= ? AND t < ? ORDER BY t",
            arrayOf(band, from.toString(), to.toString())).use { c ->
            while (c.moveToNext()) each(Reading(c.getLong(0), if (c.isNull(1)) null else c.getInt(1),
                if (c.isNull(2)) null else c.getInt(2), if (c.isNull(3)) null else c.getFloat(3), c.getInt(4) == 1,
                c.getString(5) ?: "", c.getString(6) ?: "", if (c.isNull(7)) null else c.getFloat(7),
                if (c.isNull(8)) null else c.getFloat(8), if (c.isNull(9)) null else c.getFloat(9)))
        }
    }

    /** rows and distinct days stored, for the settings page */
    fun size(): Pair<Int, Int> = readableDatabase.rawQuery(
        "SELECT COUNT(*), COUNT(DISTINCT strftime('%Y-%m-%d', t / 1000, 'unixepoch', 'localtime')) FROM reading",
        null).use { c ->
        c.moveToFirst(); c.getInt(0) to c.getInt(1)
    }

    fun clear() = io.execute {
        writableDatabase.execSQL("DELETE FROM reading")
        writableDatabase.execSQL("DELETE FROM event")
        openEvent = null
        _version.value = System.currentTimeMillis()
    }

    /** keep 30 days */
    fun prune() = io.execute {
        val cut = (System.currentTimeMillis() - 30L * 86_400_000).toString()
        writableDatabase.delete("reading", "t < ?", arrayOf(cut))
        writableDatabase.delete("event", "start < ?", arrayOf(cut))
    }

    companion object {
        fun dayStart(t: Long): Long = Calendar.getInstance().apply {
            timeInMillis = t; set(Calendar.HOUR_OF_DAY, 0); set(Calendar.MINUTE, 0)
            set(Calendar.SECOND, 0); set(Calendar.MILLISECOND, 0)
        }.timeInMillis

        fun nextDay(d: Long): Long = Calendar.getInstance().apply {
            timeInMillis = d; add(Calendar.DAY_OF_MONTH, 1)
        }.timeInMillis
    }
}
