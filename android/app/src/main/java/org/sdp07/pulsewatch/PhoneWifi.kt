package org.sdp07.pulsewatch

import android.annotation.SuppressLint
import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.net.wifi.WifiInfo
import android.net.wifi.WifiManager
import android.os.Build
import android.os.Handler
import android.os.Looper

/** the WiFi network this phone is on: name, and whether it is 5 GHz (the band is 2.4 GHz only) */
data class PhoneWifi(val ssid: String, val fiveGhz: Boolean)

/**
 * Android only reveals the network name to apps with location permission (and
 * location switched on). Calls back once, on the main thread, with null when it
 * can't tell -- the user then types the name.
 */
@SuppressLint("MissingPermission")
fun readPhoneWifi(ctx: Context, done: (PhoneWifi?) -> Unit) {
    val main = Handler(Looper.getMainLooper())
    fun clean(info: WifiInfo?): PhoneWifi? {
        val raw = info?.ssid ?: return null
        val ssid = raw.removeSurrounding("\"")
        if (ssid.isBlank() || ssid == WifiManager.UNKNOWN_SSID.removeSurrounding("<", ">") ||
            raw == WifiManager.UNKNOWN_SSID || ssid == "<unknown ssid>") return null
        return PhoneWifi(ssid, info.frequency > 4900)
    }
    if (Build.VERSION.SDK_INT < 31) {
        val wm = ctx.applicationContext.getSystemService(WifiManager::class.java)
        @Suppress("DEPRECATION")
        main.post { done(clean(wm?.connectionInfo)) }
        return
    }
    // Android 12+: the name only comes through a network callback that asks for it
    val cm = ctx.getSystemService(ConnectivityManager::class.java) ?: return main.post { done(null) }.let { }
    var answered = false
    val cb = object : ConnectivityManager.NetworkCallback(FLAG_INCLUDE_LOCATION_INFO) {
        override fun onCapabilitiesChanged(network: Network, caps: NetworkCapabilities) {
            if (answered) return
            val w = clean(caps.transportInfo as? WifiInfo) ?: return
            answered = true
            main.post { done(w); runCatching { cm.unregisterNetworkCallback(this) } }
        }
    }
    val req = NetworkRequest.Builder().addTransportType(NetworkCapabilities.TRANSPORT_WIFI).build()
    try {
        cm.registerNetworkCallback(req, cb)
    } catch (e: Exception) {
        main.post { done(null) }
        return
    }
    main.postDelayed({
        if (!answered) { answered = true; runCatching { cm.unregisterNetworkCallback(cb) }; done(null) }
    }, 2000)
}
