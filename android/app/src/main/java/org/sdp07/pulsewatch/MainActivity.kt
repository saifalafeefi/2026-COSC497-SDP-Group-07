package org.sdp07.pulsewatch

import android.annotation.SuppressLint
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.bluetooth.le.ScanFilter
import android.bluetooth.le.ScanResult
import android.companion.AssociationInfo
import android.companion.AssociationRequest
import android.companion.BluetoothLeDeviceFilter
import android.companion.CompanionDeviceManager
import android.content.Context
import android.content.Intent
import android.content.IntentSender
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.os.ParcelUuid
import android.os.Parcelable
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.IntentSenderRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.compose.runtime.LaunchedEffect
import org.sdp07.pulsewatch.ui.ExportingNote
import org.sdp07.pulsewatch.ui.HistoryScreen
import org.sdp07.pulsewatch.ui.LiveScreen
import org.sdp07.pulsewatch.ui.SettingsScreen
import org.sdp07.pulsewatch.ui.Shell
import org.sdp07.pulsewatch.ui.WifiDialog
import org.sdp07.pulsewatch.ui.PairScreen
import org.sdp07.pulsewatch.ui.PulseTheme
import org.sdp07.pulsewatch.ui.Pw

/** one link and one history for the whole process, so a rotation keeps both */
object App {
    @SuppressLint("StaticFieldLeak")   // holds the application context only
    private var link: BandLink? = null
    private var history: History? = null
    fun history(ctx: Context): History =
        history ?: History(ctx.applicationContext).also { history = it; it.prune() }
    fun link(ctx: Context): BandLink = link ?: BandLink(ctx.applicationContext).also {
        link = it
        it.history = history(ctx)
    }
}

class MainActivity : ComponentActivity() {
    private lateinit var link: BandLink
    private val prefs by lazy { getSharedPreferences("band", MODE_PRIVATE) }

    private var bandAddr by mutableStateOf<String?>(null)
    private var bandName by mutableStateOf("Pulse band")
    private var permsOk by mutableStateOf(false)
    private var btOn by mutableStateOf(true)
    private var scanning by mutableStateOf(false)
    private var message by mutableStateOf<String?>(null)
    private var demoMode by mutableStateOf(false)
    private var tab by mutableStateOf(0)
    private var wifiOpen by mutableStateOf(false)
    private var alerts by mutableStateOf(true)
    private var exporting by mutableStateOf<String?>(null)
    private lateinit var history: History
    /** waiting on the location permission to read the phone's WiFi name */
    private var wifiAsk: ((PhoneWifi?) -> Unit)? = null
    private val askLocation = registerForActivityResult(ActivityResultContracts.RequestPermission()) { ok ->
        val cb = wifiAsk ?: return@registerForActivityResult
        wifiAsk = null
        if (ok) readPhoneWifi(this, cb) else cb(null)
    }

    private val askPerms = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
        refresh()
        if (!permsOk) message = "Pulse Watch needs Bluetooth permission to reach the band."
    }
    private val askBtOn = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { refresh() }
    private val pairSheet = registerForActivityResult(ActivityResultContracts.StartIntentSenderForResult()) { res ->
        if (res.resultCode != RESULT_OK || res.data == null) {
            message = "No band picked. Try again, or scan instead."
            return@registerForActivityResult
        }
        val (addr, name) = pickedFrom(res.data!!)
        if (addr == null) message = "Couldn't read that band's address. Try scanning instead."
        else keepBand(addr, name ?: "Pulse band")
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // dark ink behind both bars, light icons on it, whatever the phone's own theme
        enableEdgeToEdge(
            statusBarStyle = SystemBarStyle.dark(android.graphics.Color.TRANSPARENT),
            navigationBarStyle = SystemBarStyle.dark(android.graphics.Color.TRANSPARENT))
        link = App.link(this)
        history = App.history(this)
        alerts = prefs.getBoolean("alerts", true)
        link.alertsOn = alerts
        bandAddr = prefs.getString("addr", null)
        bandName = prefs.getString("name", "Pulse band") ?: "Pulse band"
        link.bandName = bandName
        refresh()
        if (!permsOk) askPerms.launch(wantedPermissions())

        setContent {
            PulseTheme {
                Box(Modifier.fillMaxSize().background(Pw.ink)) {
                    val found by link.found.collectAsStateWithLifecycle()
                    val addr = bandAddr
                    if (addr == null && !demoMode) {
                        val (blocker, action) = when {
                            !permsOk -> "Pulse Watch talks to the band over Bluetooth. Allow it to find " +
                                    "and connect to nearby devices." to "Allow Bluetooth"
                            !btOn -> "Bluetooth is off." to "Turn on Bluetooth"
                            else -> null to null
                        }
                        PairScreen(
                            canPair = permsOk && btOn,
                            blocker = blocker, blockerAction = action,
                            onBlocker = { if (!permsOk) askPerms.launch(wantedPermissions()) else askBluetoothOn() },
                            onPairSheet = { openPairSheet() },
                            onScan = { scanning = true; message = null; link.startScan() },
                            scanning = scanning,
                            found = found,
                            onPick = { b -> link.stopScan(); scanning = false; keepBand(b.address, b.name) },
                            message = message,
                            onDemo = { link.stopScan(); scanning = false; demoMode = true; link.startDemo() },
                        )
                    } else {
                        val name = if (demoMode) "Demo band" else bandName
                        Shell(tab, { tab = it }) {
                            when (tab) {
                                0 -> LiveScreen(link = link, bandName = name, demo = demoMode,
                                    onWifi = { wifiOpen = true })
                                1 -> HistoryScreen(history, name, onExport = { exportPdf(name) })
                                else -> SettingsScreen(
                                    link = link, history = history, bandName = name,
                                    address = if (demoMode) null else bandAddr, demo = demoMode,
                                    version = appVersion(), alerts = alerts,
                                    onAlerts = { on -> alerts = on; link.alertsOn = on
                                        prefs.edit().putBoolean("alerts", on).apply() },
                                    onWifi = { wifiOpen = true },
                                    onSensitivity = { v -> link.send("sens=%.3f".format(java.util.Locale.US, v)) },
                                    onForget = { if (demoMode) { link.disconnect(); demoMode = false; tab = 0 } else forget() },
                                    onExportPdf = { exportPdf(name) },
                                    onExportCsv = { exportCsv(name) },
                                )
                            }
                        }
                        // a band that has never been on any network: offer this phone's, once
                        val net by link.net.collectAsStateWithLifecycle()
                        LaunchedEffect(net?.state, net?.saved, bandAddr) {
                            val n = net
                            val key = "wifiOffered:" + bandAddr
                            if (!demoMode && n != null && n.state == "off" && n.saved == 0 &&
                                !prefs.getBoolean(key, false)) {
                                prefs.edit().putBoolean(key, true).apply()
                                wifiOpen = true
                            }
                        }
                        if (wifiOpen) WifiDialog(link, name, onUsePhone = { ask, done -> phoneWifi(ask, done) },
                            onClose = { wifiOpen = false; link.clearWifiStep() })
                        exporting?.let { ExportingNote(it) }
                    }
                }
            }
        }
    }

    override fun onStart() {
        super.onStart()
        refresh()
        val a = bandAddr
        if (a != null && permsOk && !demoMode && link.state.value == LinkState.Idle) link.connect(a)
    }

    override fun onDestroy() {
        super.onDestroy()
        if (isFinishing) link.disconnect()
    }

    // ---------------------------------------------------------------- WiFi + export

    /** the phone's WiFi name; asks for location only when [ask] and not granted yet */
    private fun phoneWifi(ask: Boolean, done: (PhoneWifi?) -> Unit) {
        val granted = ContextCompat.checkSelfPermission(this, android.Manifest.permission.ACCESS_FINE_LOCATION) ==
                PackageManager.PERMISSION_GRANTED
        when {
            granted -> readPhoneWifi(this, done)
            ask -> { wifiAsk = done; askLocation.launch(android.Manifest.permission.ACCESS_FINE_LOCATION) }
            else -> done(null)
        }
    }

    private fun exportPdf(band: String) = runExport("Making your report") {
        val days = history.days(band, 7)
        if (days.isEmpty()) null
        else listOf(Export.pdf(this, history, band, days)) to "application/pdf"
    }

    private fun exportCsv(band: String) = runExport("Writing your data") {
        val days = history.days(band, 30)
        if (days.isEmpty()) null
        else Export.csv(this, history, band, days.min(), History.nextDay(days.max())) to "text/csv"
    }

    private fun runExport(what: String, make: () -> Pair<List<java.io.File>, String>?) {
        if (exporting != null) return
        exporting = what
        Thread {
            val r = runCatching { make() }
            runOnUiThread {
                exporting = null
                val v = r.getOrNull()
                if (v == null) {
                    android.widget.Toast.makeText(this, r.exceptionOrNull()?.let { "Export failed: ${it.message}" }
                        ?: "Nothing recorded yet", android.widget.Toast.LENGTH_LONG).show()
                } else Export.share(this, v.first, v.second, "Pulse Watch: " + (if (demoMode) "Demo band" else bandName))
            }
        }.start()
    }

    private fun appVersion(): String = runCatching {
        packageManager.getPackageInfo(packageName, 0).versionName ?: ""
    }.getOrDefault("")

    // ---------------------------------------------------------------- state

    private fun refresh() {
        permsOk = link.hasBtPermissions()
        btOn = link.bluetoothOn
    }

    private fun wantedPermissions(): Array<String> {
        val p = BandLink.btPermissions().toMutableList()
        if (Build.VERSION.SDK_INT >= 33) p += android.Manifest.permission.POST_NOTIFICATIONS
        return p.toTypedArray()
    }

    private fun keepBand(addr: String, name: String) {
        prefs.edit().putString("addr", addr).putString("name", name).apply()
        bandAddr = addr
        bandName = name
        link.bandName = name
        message = null
        link.connect(addr)
    }

    private fun forget() {
        link.disconnect()
        prefs.edit().remove("addr").remove("name").apply()   // keep the alert setting
        bandAddr = null
        scanning = false
        tab = 0
    }

    @SuppressLint("MissingPermission")
    private fun askBluetoothOn() {
        if (ContextCompat.checkSelfPermission(this, android.Manifest.permission.BLUETOOTH_CONNECT) ==
            PackageManager.PERMISSION_GRANTED || Build.VERSION.SDK_INT < 31)
            askBtOn.launch(Intent(BluetoothAdapter.ACTION_REQUEST_ENABLE))
    }

    // ---------------------------------------------------------------- pairing sheet
    // Android's own "pair a nearby device" sheet, filtered on the band's service:
    // the earbuds-style flow, with no scanning permission needed

    private fun openPairSheet() {
        message = null
        val cdm = getSystemService(CompanionDeviceManager::class.java)
        if (cdm == null || !packageManager.hasSystemFeature(PackageManager.FEATURE_COMPANION_DEVICE_SETUP)) {
            scanning = true; link.startScan()     // no sheet on this phone: scan in the app instead
            return
        }
        val filter = BluetoothLeDeviceFilter.Builder()
            .setScanFilter(ScanFilter.Builder().setServiceUuid(ParcelUuid(Uuids.SVC)).build())
            .build()
        val req = AssociationRequest.Builder().addDeviceFilter(filter).setSingleDevice(false).build()
        val cb = object : CompanionDeviceManager.Callback() {
            override fun onAssociationPending(intentSender: IntentSender) = launchSheet(intentSender)

            @Deprecated("Android 12 and below")
            override fun onDeviceFound(intentSender: IntentSender) = launchSheet(intentSender)

            override fun onAssociationCreated(associationInfo: AssociationInfo) {}

            override fun onFailure(error: CharSequence?) {
                runOnUiThread { message = "No band found nearby. Is it on? You can also scan instead." }
            }
        }
        try {
            if (Build.VERSION.SDK_INT >= 33) cdm.associate(req, mainExecutor, cb)
            else @Suppress("DEPRECATION") cdm.associate(req, cb, null)
        } catch (e: Exception) {
            scanning = true; link.startScan()
        }
    }

    private fun launchSheet(s: IntentSender) {
        runOnUiThread { pairSheet.launch(IntentSenderRequest.Builder(s).build()) }
    }

    @SuppressLint("MissingPermission")
    private fun pickedFrom(data: Intent): Pair<String?, String?> {
        if (Build.VERSION.SDK_INT >= 33) {
            val info = data.getParcelableExtra(CompanionDeviceManager.EXTRA_ASSOCIATION, AssociationInfo::class.java)
            val mac = info?.deviceMacAddress?.toString()?.uppercase()
            if (mac != null) return mac to (info.displayName?.toString())
        }
        @Suppress("DEPRECATION")
        val d: Parcelable? = data.getParcelableExtra(CompanionDeviceManager.EXTRA_DEVICE)
        return when (d) {
            is ScanResult -> d.device.address to (d.scanRecord?.deviceName ?: d.device.name)
            is BluetoothDevice -> d.address to d.name
            else -> null to null
        }
    }
}
