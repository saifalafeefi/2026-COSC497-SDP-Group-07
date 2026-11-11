package org.sdp07.pulsewatch

import org.json.JSONObject

/** One second of what the band reports over Bluetooth (board_ble.h, STATUS). */
data class BandStatus(
    val id: String,
    val subject: String,
    val hr: Int?,
    val spo2: Int?,
    /** on | check | unsteady | nopulse | none */
    val finger: String,
    /** ok | zero | warm | hold | none | idle | off | stale */
    val state: String,
    /** master | board | demo | none: who worked the verdict out */
    val source: String,
    val level: Float?,
    val flag: Boolean,
    val threshold: Float,
    val waitS: Int,
    val bandState: String,
    val bandLevel: Float?,
    val bandFlag: Boolean,
    val aiState: String,
    val aiLevel: Float?,
    val aiFlag: Boolean,
    val aiMs: Int,
    val motion: Float?,
    val sensitivity: Float,
    val wifiRssi: Int,
    val uptimeS: Long,
) {
    companion object {
        fun parse(bytes: ByteArray): BandStatus? = try {
            val j = JSONObject(String(bytes, Charsets.UTF_8))
            val od = j.optJSONObject("od") ?: JSONObject()
            val ae = j.optJSONObject("ae") ?: JSONObject()
            BandStatus(
                id = j.optString("id"),
                subject = j.optString("sub"),
                hr = j.intOrNull("hr"),
                spo2 = j.intOrNull("sp"),
                finger = j.optString("fi", "none").substringBefore(':'),
                state = j.optString("st", "none"),
                source = j.optString("src", "none"),
                level = j.floatOrNull("lv"),
                flag = j.optInt("fl", 0) == 1,
                threshold = j.optDouble("thr", 0.66).toFloat(),
                waitS = j.optInt("w", 0),
                bandState = od.optString("st", "off"),
                bandLevel = od.floatOrNull("l"),
                bandFlag = od.optInt("f", 0) == 1,
                aiState = ae.optString("st", "none"),
                aiLevel = ae.floatOrNull("l"),
                aiFlag = ae.optInt("f", 0) == 1,
                aiMs = ae.optInt("ms", 0),
                motion = j.floatOrNull("mo"),
                sensitivity = j.optDouble("sens", 0.5).toFloat(),
                wifiRssi = j.optInt("rs", 0),
                uptimeS = j.optLong("up", 0),
            )
        } catch (e: Exception) {
            null
        }

        private fun JSONObject.intOrNull(k: String): Int? =
            if (!has(k) || isNull(k)) null else optInt(k)

        private fun JSONObject.floatOrNull(k: String): Float? =
            if (!has(k) || isNull(k)) null else optDouble(k).toFloat()
    }
}

/** The band's INFO notification: the finger check's progress and the reason
 *  that goes with the verdict (board_ble.h, INFO). */
data class BandInfo(
    /** on | check | unsteady | nopulse | none */
    val finger: String,
    /** seconds of the finger check passed, out of [fingerOf] */
    val fingerN: Int,
    val fingerOf: Int,
    /** seconds left before a lifted finger's window is dropped */
    val keepS: Int,
    /** "settling in — 7 of 20 s", "skipped 6 s of movement", or "" */
    val why: String,
    val wifiRssi: Int,
    val uptimeS: Long,
    /** was the wearer calibrated on the master: 1 yes, 0 no, -1 the band doesn't know yet */
    val calibrated: Int = -1,
) {
    companion object {
        fun parse(bytes: ByteArray): BandInfo? = try {
            val j = JSONObject(String(bytes, Charsets.UTF_8))
            BandInfo(
                finger = j.optString("fc", "none"),
                fingerN = j.optInt("fn", 0),
                fingerOf = j.optInt("fo", 5).coerceAtLeast(1),
                keepS = j.optInt("fk", 0),
                why = j.optString("why", ""),
                wifiRssi = j.optInt("rs", 0),
                uptimeS = j.optLong("up", 0),
                calibrated = j.optInt("cal", -1),
            )
        } catch (e: Exception) {
            null
        }
    }
}

/** The band's NET notification: where it is on WiFi (board_ble.h, NET). */
data class BandNet(
    /** on | joining | off (off = no network saved at all) */
    val state: String,
    val ssid: String,
    val ip: String,
    val rssi: Int,
    val saved: Int,
    /** the last join the phone asked for: "" | pending | ok | fail */
    val joinResult: String,
    val joinSsid: String,
) {
    companion object {
        fun parse(bytes: ByteArray): BandNet? = try {
            val j = JSONObject(String(bytes, Charsets.UTF_8))
            BandNet(j.optString("st", "off"), j.optString("ss"), j.optString("ip"), j.optInt("rs", 0),
                j.optInt("n", 0), j.optString("jr"), j.optString("js"))
        } catch (e: Exception) {
            null
        }
    }
}
