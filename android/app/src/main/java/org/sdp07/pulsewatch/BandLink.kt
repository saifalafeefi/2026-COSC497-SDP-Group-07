package org.sdp07.pulsewatch

import android.annotation.SuppressLint
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCallback
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothGattDescriptor
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothProfile
import android.bluetooth.BluetoothStatusCodes
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanFilter
import android.bluetooth.le.ScanResult
import android.bluetooth.le.ScanSettings
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.ParcelUuid
import android.os.SystemClock
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import java.util.UUID

object Uuids {
    val SVC: UUID = UUID.fromString("7a1e0001-6b2c-4f1a-9d3e-70756c736521")
    val STAT: UUID = UUID.fromString("7a1e0002-6b2c-4f1a-9d3e-70756c736521")
    val WAVE: UUID = UUID.fromString("7a1e0003-6b2c-4f1a-9d3e-70756c736521")
    val CMD: UUID = UUID.fromString("7a1e0004-6b2c-4f1a-9d3e-70756c736521")
    val INFO: UUID = UUID.fromString("7a1e0005-6b2c-4f1a-9d3e-70756c736521")
    val WIFI: UUID = UUID.fromString("7a1e0006-6b2c-4f1a-9d3e-70756c736521")
    val NET: UUID = UUID.fromString("7a1e0007-6b2c-4f1a-9d3e-70756c736521")
    val CCCD: UUID = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")
}

enum class LinkState { Idle, Searching, Connecting, Connected, Lost }

data class FoundBand(val address: String, val name: String, val rssi: Int)

/** The 64 Hz conditioned pulse, as the band streams it: the newest ~12 s. */
class WaveBuffer(private val cap: Int = 64 * 12) {
    private val v = FloatArray(cap)
    /** index (the band's sample count) one past the newest sample held */
    @Volatile var end: Long = -1L
        private set
    @Volatile var firstHeld: Long = 0L
        private set
    @Volatile var receivedAt: Long = 0L
        private set

    @Synchronized fun add(first: Long, samples: ShortArray) {
        if (end < 0 || first < end - cap || first > end + cap) {
            firstHeld = first                       // first packet, or a jump: start over
        } else if (first > end) {
            for (i in end until first) v[(i % cap).toInt()] = Float.NaN   // a gap: draw a break
        }
        for (k in samples.indices) v[((first + k) % cap).toInt()] = samples[k].toFloat()
        end = first + samples.size
        if (end - firstHeld > cap) firstHeld = end - cap
        receivedAt = SystemClock.uptimeMillis()
    }

    /** samples [from, to) that are still held, NaN where missing */
    @Synchronized fun copy(from: Long, to: Long, out: FloatArray): Int {
        var n = 0
        var i = from
        while (i < to && n < out.size) {
            out[n++] = if (i < firstHeld || i >= end) Float.NaN else v[(i % cap).toInt()]
            i++
        }
        return n
    }

    @Synchronized fun clear() {
        end = -1L; firstHeld = 0L
    }
}

/**
 * The Bluetooth link to one band: connect, keep reconnecting, stream the status
 * and the waveform, send commands. GATT allows one operation at a time, so the
 * setup steps go through a small queue on the main thread.
 */
@SuppressLint("MissingPermission")   // every entry point is gated on hasBtPermissions()
class BandLink(private val ctx: Context) {
    private val main = Handler(Looper.getMainLooper())
    private val btManager = ctx.getSystemService(BluetoothManager::class.java)

    private val _state = MutableStateFlow(LinkState.Idle)
    val state: StateFlow<LinkState> = _state
    private val _status = MutableStateFlow<BandStatus?>(null)
    val status: StateFlow<BandStatus?> = _status
    private val _info = MutableStateFlow<BandInfo?>(null)
    val info: StateFlow<BandInfo?> = _info
    private val _net = MutableStateFlow<BandNet?>(null)
    /** where the band is on WiFi; null until it says (or old firmware) */
    val net: StateFlow<BandNet?> = _net
    private val _wifiStep = MutableStateFlow<String?>(null)
    /** sending WiFi details: null | pairing | sending | sent | error:<why> */
    val wifiStep: StateFlow<String?> = _wifiStep
    private val _found = MutableStateFlow<List<FoundBand>>(emptyList())
    val found: StateFlow<List<FoundBand>> = _found
    /** heart rate over the last 10 minutes: (uptime ms, bpm) */
    private val _hrHistory = MutableStateFlow<List<Pair<Long, Int>>>(emptyList())
    val hrHistory: StateFlow<List<Pair<Long, Int>>> = _hrHistory
    val wave = WaveBuffer()
    /** the paired band's name, for notifications and the history */
    var bandName: String = "Pulse band"
    /** where readings are kept; set by App */
    var history: History? = null
    /** notify when the band flags (Settings) */
    var alertsOn: Boolean = true

    private var gatt: BluetoothGatt? = null
    private var address: String? = null
    private var want = false
    private var statusAt = 0L
    private var lastFlag = false
    private val ops = ArrayDeque<() -> Boolean>()
    private var busy = false
    private val opTimeout = Runnable { busy = false; next() }

    val bluetoothOn: Boolean get() = btManager?.adapter?.isEnabled == true

    fun hasBtPermissions(): Boolean =
        btPermissions().all {
            ContextCompat.checkSelfPermission(ctx, it) == PackageManager.PERMISSION_GRANTED
        }

    // ---------------------------------------------------------------- connect

    fun connect(addr: String) {
        address = addr
        want = true
        open()
    }

    fun disconnect() {
        want = false
        demo = false
        main.removeCallbacksAndMessages(null)
        gatt?.let { it.disconnect(); it.close() }
        gatt = null
        ops.clear(); busy = false
        _state.value = LinkState.Idle
        _status.value = null
        _info.value = null
        _net.value = null
        _wifiStep.value = null
        _hrHistory.value = emptyList()
        history?.interrupt()
        lastFlag = false
        unregisterBond()
        wave.clear()
    }

    // ---------------------------------------------------------------- demo
    // a simulated band, for showing the app with no hardware: two minutes of
    // calm, a stress rise that crosses the line and flags, then recovery

    var demo = false
        private set
    private var demoT0 = 0L
    private var demoIdx = 0L
    private var demoPhase = 0.0
    private var demoSens = 0.5f
    private val rnd = java.util.Random(7)

    fun startDemo() {
        disconnect()
        demo = true
        bandName = "Demo band"
        demoT0 = SystemClock.uptimeMillis()
        demoIdx = 0L
        demoPhase = 0.0
        _state.value = LinkState.Connected
        _net.value = BandNet("on", "DemoNet", "192.168.1.57", -52, 1, "", "")
        main.post(demoWave)
        main.post(demoStatus)
    }

    /** 0 calm .. 1 peak stress, over a 150 s loop */
    private fun demoStress(tS: Double): Double {
        val t = tS % 150.0
        return when {
            t < 60 -> 0.0
            t < 90 -> (t - 60) / 30.0
            t < 115 -> 1.0
            else -> 1.0 - (t - 115) / 35.0
        }.coerceIn(0.0, 1.0)
    }

    private fun demoHr(tS: Double) = 68.0 + 24.0 * demoStress(tS) + 1.5 * Math.sin(tS / 7.0)

    private val demoWave = object : Runnable {
        override fun run() {
            if (!demo) return
            val n = 8
            val s = ShortArray(n)
            for (k in 0 until n) {
                val tS = (demoIdx + k) / 64.0
                demoPhase += demoHr(tS) / 60.0 / 64.0
                val p = demoPhase % 1.0
                // a fingertip pulse: a sharp systolic peak, a dicrotic bump after it
                val v = 900 * Math.exp(-Math.pow((p - 0.18) / 0.07, 2.0)) +
                        260 * Math.exp(-Math.pow((p - 0.47) / 0.08, 2.0)) - 330 +
                        25 * rnd.nextGaussian()
                s[k] = v.toInt().toShort()
            }
            wave.add(demoIdx, s)
            demoIdx += n
            main.postDelayed(this, 125)
        }
    }

    private val demoStatus = object : Runnable {
        override fun run() {
            if (!demo) return
            val tS = (SystemClock.uptimeMillis() - demoT0) / 1000.0
            val checking = tS < 5
            val warm = tS < 14
            val learning = tS >= 8 && warm
            val why = when {
                checking -> ""
                !learning && warm -> "filling the first 60 s window — ${(8 - tS).toInt().coerceAtLeast(1)} s left"
                learning -> "settling in — ${((tS - 8) * 20 / 6).toInt()} of 20 s"
                tS % 150 in 92.0..104.0 -> "skipped 4 s of movement"
                else -> ""
            }
            _info.value = BandInfo(if (checking) "check" else "on", tS.toInt().coerceIn(0, 5), 5, 0,
                why, 0, tS.toLong())
            val st = demoStress(tS)
            val thr = 0.62f + 0.2f * (0.5f - demoSens)
            val lv = (0.22 + 0.7 * st + 0.04 * rnd.nextGaussian()).coerceIn(0.0, 1.0).toFloat()
            val flag = !warm && lv > thr && st > 0.75
            onStatus(BandStatus(
                id = "demo", subject = "Demo", hr = demoHr(tS).toInt(), spo2 = 98 - (st * 1.5).toInt(),
                finger = if (checking) "check" else "on", state = if (warm) "warm" else "ok", source = "board",
                level = if (warm) null else lv, flag = flag, threshold = thr,
                waitS = if (!warm) 0 else if (learning) (14 - tS).toInt().coerceAtLeast(1) else (8 - tS).toInt().coerceAtLeast(1),
                bandState = if (warm) "warm" else "ok", bandLevel = if (warm) null else (lv * 0.95f),
                bandFlag = flag, aiState = if (warm) "wait" else "ok",
                aiLevel = if (warm) null else (lv * 0.9f + 0.03f), aiFlag = flag && lv > thr + 0.05f,
                aiMs = 445, motion = 0.02f, sensitivity = demoSens, wifiRssi = 0,
                uptimeS = tS.toLong()))
            main.postDelayed(this, 1000)
        }
    }

    private fun open() {
        val addr = address ?: return
        if (!hasBtPermissions() || !bluetoothOn) {
            _state.value = LinkState.Lost
            retryLater(3000)
            return
        }
        val dev = btManager?.adapter?.getRemoteDevice(addr) ?: return
        _state.value = LinkState.Connecting
        gatt?.close()
        ops.clear(); busy = false
        gatt = dev.connectGatt(ctx, false, cb, BluetoothDevice.TRANSPORT_LE)
        // a connect attempt to a band that is off never calls back: give up and retry
        main.postDelayed(connectWatchdog, 12000)
    }

    private val connectWatchdog = Runnable {
        if (_state.value == LinkState.Connecting) {
            gatt?.close(); gatt = null
            _state.value = LinkState.Lost
            retryLater(1500)
        }
    }

    private fun retryLater(ms: Long) {
        if (want) main.postDelayed({ if (want && _state.value != LinkState.Connected) open() }, ms)
    }

    private val cb = object : BluetoothGattCallback() {
        override fun onConnectionStateChange(g: BluetoothGatt, status: Int, newState: Int) {
            main.post {
                if (g != gatt) return@post
                if (newState == BluetoothProfile.STATE_CONNECTED && status == BluetoothGatt.GATT_SUCCESS) {
                    main.removeCallbacks(connectWatchdog)
                    g.requestConnectionPriority(BluetoothGatt.CONNECTION_PRIORITY_HIGH)
                    if (!g.requestMtu(256)) g.discoverServices()
                } else {
                    main.removeCallbacks(connectWatchdog)
                    g.close()
                    gatt = null
                    ops.clear(); busy = false
                    history?.interrupt()
                    _state.value = if (want) LinkState.Lost else LinkState.Idle
                    retryLater(2000)
                }
            }
        }

        override fun onMtuChanged(g: BluetoothGatt, mtu: Int, status: Int) {
            main.post { if (g == gatt) g.discoverServices() }
        }

        override fun onServicesDiscovered(g: BluetoothGatt, status: Int) {
            main.post {
                if (g != gatt) return@post
                val svc = g.getService(Uuids.SVC)
                if (svc == null) {           // not a Pulse band, or an old firmware
                    g.disconnect()
                    return@post
                }
                svc.getCharacteristic(Uuids.STAT)?.let { enableNotify(g, it); read(g, it) }
                svc.getCharacteristic(Uuids.WAVE)?.let { enableNotify(g, it) }
                // older firmware has no INFO: the app then simply shows less
                svc.getCharacteristic(Uuids.INFO)?.let { enableNotify(g, it); read(g, it) }
                svc.getCharacteristic(Uuids.NET)?.let { enableNotify(g, it); read(g, it) }
                _state.value = LinkState.Connected
            }
        }

        override fun onDescriptorWrite(g: BluetoothGatt, d: BluetoothGattDescriptor, status: Int) = opDone()
        override fun onCharacteristicWrite(g: BluetoothGatt, c: BluetoothGattCharacteristic, status: Int) {
            if (c.uuid == Uuids.WIFI) _wifiStep.value =
                if (status == BluetoothGatt.GATT_SUCCESS) "sent" else "error:The band refused the details (status $status)"
            opDone()
        }

        override fun onCharacteristicRead(g: BluetoothGatt, c: BluetoothGattCharacteristic,
                                          value: ByteArray, status: Int) {
            if (status == BluetoothGatt.GATT_SUCCESS) handle(c.uuid, value)
            opDone()
        }

        @Deprecated("Android 12 and below")
        override fun onCharacteristicRead(g: BluetoothGatt, c: BluetoothGattCharacteristic, status: Int) {
            if (Build.VERSION.SDK_INT < 33) {
                @Suppress("DEPRECATION")
                if (status == BluetoothGatt.GATT_SUCCESS) c.value?.let { handle(c.uuid, it) }
                opDone()
            }
        }

        override fun onCharacteristicChanged(g: BluetoothGatt, c: BluetoothGattCharacteristic, value: ByteArray) =
            handle(c.uuid, value)

        @Deprecated("Android 12 and below")
        override fun onCharacteristicChanged(g: BluetoothGatt, c: BluetoothGattCharacteristic) {
            @Suppress("DEPRECATION")
            if (Build.VERSION.SDK_INT < 33) c.value?.let { handle(c.uuid, it) }
        }
    }

    private fun handle(uuid: UUID, v: ByteArray) {
        when (uuid) {
            Uuids.STAT -> BandStatus.parse(v)?.let { onStatus(it) }
            Uuids.INFO -> BandInfo.parse(v)?.let { _info.value = it }
            Uuids.NET -> BandNet.parse(v)?.let { _net.value = it }
            Uuids.WAVE -> if (v.size >= 6) {
                val first = (v[0].toLong() and 0xFF) or ((v[1].toLong() and 0xFF) shl 8) or
                        ((v[2].toLong() and 0xFF) shl 16) or ((v[3].toLong() and 0xFF) shl 24)
                val n = (v.size - 4) / 2
                val s = ShortArray(n) { k ->
                    ((v[4 + 2 * k].toInt() and 0xFF) or (v[5 + 2 * k].toInt() shl 8)).toShort()
                }
                wave.add(first, s)
            }
        }
    }

    private fun onStatus(s: BandStatus) {
        _status.value = s
        history?.record(bandName, s)
        val now = SystemClock.uptimeMillis()
        statusAt = now
        if (s.hr != null) {
            val keep = _hrHistory.value.filter { now - it.first < 10 * 60_000 }
            _hrHistory.value = keep + (now to s.hr)
        }
        if (s.flag && !lastFlag) notifyFlag(s)
        lastFlag = s.flag
    }

    /** seconds since the last status, for the "stale" badge */
    fun statusAgeS(): Float = if (statusAt == 0L) Float.MAX_VALUE else
        (SystemClock.uptimeMillis() - statusAt) / 1000f

    // ---------------------------------------------------------------- GATT queue

    private fun enqueue(op: () -> Boolean) {
        ops.addLast(op)
        if (!busy) next()
    }

    private fun next() {
        main.removeCallbacks(opTimeout)
        val op = ops.removeFirstOrNull() ?: run { busy = false; return }
        busy = true
        if (!op()) { busy = false; next(); return }
        main.postDelayed(opTimeout, 3000)
    }

    private fun opDone() {
        main.post { main.removeCallbacks(opTimeout); busy = false; next() }
    }

    private fun enableNotify(g: BluetoothGatt, c: BluetoothGattCharacteristic) = enqueue {
        g.setCharacteristicNotification(c, true)
        val d = c.getDescriptor(Uuids.CCCD) ?: return@enqueue false
        val on = BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE
        if (Build.VERSION.SDK_INT >= 33) g.writeDescriptor(d, on) == BluetoothStatusCodes.SUCCESS
        else {
            @Suppress("DEPRECATION")
            d.value = on
            @Suppress("DEPRECATION")
            g.writeDescriptor(d)
        }
    }

    private fun read(g: BluetoothGatt, c: BluetoothGattCharacteristic) = enqueue { g.readCharacteristic(c) }

    /** "sens=0.62" or "id=5" -- see board_ble.h */
    fun send(cmd: String) {
        if (demo) {
            if (cmd.startsWith("sens=")) cmd.substring(5).toFloatOrNull()?.let { demoSens = it.coerceIn(0f, 1f) }
            return
        }
        val g = gatt ?: return
        val c = g.getService(Uuids.SVC)?.getCharacteristic(Uuids.CMD) ?: return
        val bytes = cmd.toByteArray(Charsets.UTF_8)
        enqueue {
            if (Build.VERSION.SDK_INT >= 33)
                g.writeCharacteristic(c, bytes, BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT) ==
                        BluetoothStatusCodes.SUCCESS
            else {
                @Suppress("DEPRECATION")
                c.value = bytes
                c.writeType = BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT
                @Suppress("DEPRECATION")
                g.writeCharacteristic(c)
            }
        }
    }

    // ---------------------------------------------------------------- WiFi setup
    // The band's WIFI characteristic only accepts encrypted writes, so the phone
    // pairs with it first: Android shows its own "Pair with Pulse …?" prompt,
    // once, and the password never travels in the clear.

    private var bondReceiver: BroadcastReceiver? = null
    private val bondTimeout = Runnable {
        if (_wifiStep.value == "pairing") failWifi("Pairing didn't finish. Try again.")
    }

    fun sendWifi(ssid: String, pass: String) {
        if (demo) {                                  // pretend, so the flow can be shown
            _wifiStep.value = "sent"
            _net.value = BandNet("joining", "", "", 0, 1, "pending", ssid)
            main.postDelayed({
                if (demo) _net.value = BandNet("on", ssid, "192.168.1.57", -52, 1, "ok", ssid)
            }, 3000)
            return
        }
        val g = gatt ?: return failWifi("The band isn't connected.")
        // forget the last attempt's answer until the band reports on this one
        _net.value = _net.value?.copy(joinResult = "pending", joinSsid = ssid)
        val dev = g.device
        if (dev.bondState == BluetoothDevice.BOND_BONDED) { writeWifi(ssid, pass); return }
        _wifiStep.value = "pairing"
        unregisterBond()
        val r = object : BroadcastReceiver() {
            override fun onReceive(c: Context, i: Intent) {
                @Suppress("DEPRECATION")
                val d: BluetoothDevice? = if (Build.VERSION.SDK_INT >= 33)
                    i.getParcelableExtra(BluetoothDevice.EXTRA_DEVICE, BluetoothDevice::class.java)
                else i.getParcelableExtra(BluetoothDevice.EXTRA_DEVICE)
                if (d?.address != dev.address) return
                when (i.getIntExtra(BluetoothDevice.EXTRA_BOND_STATE, BluetoothDevice.BOND_NONE)) {
                    BluetoothDevice.BOND_BONDED -> {
                        unregisterBond(); main.removeCallbacks(bondTimeout)
                        // let the link finish encrypting before the write
                        main.postDelayed({ writeWifi(ssid, pass) }, 600)
                    }
                    BluetoothDevice.BOND_NONE -> {
                        unregisterBond(); main.removeCallbacks(bondTimeout)
                        failWifi("Pairing was cancelled. The band needs it to receive a password safely.")
                    }
                }
            }
        }
        bondReceiver = r
        ContextCompat.registerReceiver(ctx, r, IntentFilter(BluetoothDevice.ACTION_BOND_STATE_CHANGED),
            ContextCompat.RECEIVER_NOT_EXPORTED)
        main.postDelayed(bondTimeout, 45_000)
        if (!dev.createBond()) { unregisterBond(); failWifi("Couldn't start pairing with the band.") }
    }

    private fun writeWifi(ssid: String, pass: String) {
        val g = gatt ?: return failWifi("The band disconnected.")
        val c = g.getService(Uuids.SVC)?.getCharacteristic(Uuids.WIFI)
            ?: return failWifi("This band's firmware can't take WiFi details over Bluetooth yet.")
        val bytes = ("J\u001F" + ssid + "\u001F" + pass).toByteArray(Charsets.UTF_8)
        _wifiStep.value = "sending"
        enqueue {
            val ok = if (Build.VERSION.SDK_INT >= 33)
                g.writeCharacteristic(c, bytes, BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT) ==
                        BluetoothStatusCodes.SUCCESS
            else {
                @Suppress("DEPRECATION")
                c.value = bytes
                c.writeType = BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT
                @Suppress("DEPRECATION")
                g.writeCharacteristic(c)
            }
            if (!ok) failWifi("Couldn't send the details. Try again.")
            ok
        }
    }

    private fun failWifi(why: String) { _wifiStep.value = "error:$why" }

    fun clearWifiStep() { _wifiStep.value = null }

    private fun unregisterBond() {
        bondReceiver?.let { runCatching { ctx.unregisterReceiver(it) } }
        bondReceiver = null
    }

    // ---------------------------------------------------------------- scanning
    // the fallback when the system pairing sheet is unavailable: list nearby bands

    private val scanCb = object : ScanCallback() {
        override fun onScanResult(type: Int, r: ScanResult) {
            val name = r.scanRecord?.deviceName ?: r.device.name ?: "Pulse band"
            val f = FoundBand(r.device.address, name, r.rssi)
            _found.value = (_found.value.filter { it.address != f.address } + f).sortedByDescending { it.rssi }
        }
    }

    fun startScan() {
        if (!hasBtPermissions() || !bluetoothOn) return
        _found.value = emptyList()
        val sc = btManager?.adapter?.bluetoothLeScanner ?: return
        val filter = ScanFilter.Builder().setServiceUuid(ParcelUuid(Uuids.SVC)).build()
        val settings = ScanSettings.Builder().setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY).build()
        sc.startScan(listOf(filter), settings, scanCb)
        main.postDelayed({ stopScan() }, 15000)
    }

    fun stopScan() {
        if (!hasBtPermissions() || !bluetoothOn) return
        btManager?.adapter?.bluetoothLeScanner?.stopScan(scanCb)
    }

    // ---------------------------------------------------------------- alerts

    private fun notifyFlag(s: BandStatus) {
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(ctx, android.Manifest.permission.POST_NOTIFICATIONS) !=
            PackageManager.PERMISSION_GRANTED) return
        val nm = ctx.getSystemService(NotificationManager::class.java) ?: return
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL, "Band flags", NotificationManager.IMPORTANCE_HIGH).apply {
                description = "When the band flags a sustained rise above your calm"
            })
        val open = PendingIntent.getActivity(
            ctx, 0, Intent(ctx, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE)
        if (!alertsOn) return
        val who = if (s.source == "master") "the master" else "the band"
        val n = NotificationCompat.Builder(ctx, CHANNEL)
            .setSmallIcon(android.R.drawable.stat_notify_error)
            .setContentTitle("$bandName: flagged")
            .setContentText("Heart rate ${s.hr ?: "--"} bpm, above your calm. Flagged by $who; take a moment.")
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setContentIntent(open)
            .setAutoCancel(true)
            .build()
        nm.notify(1, n)
    }

    companion object {
        private const val CHANNEL = "flags"

        fun btPermissions(): Array<String> =
            if (Build.VERSION.SDK_INT >= 31) arrayOf(
                android.Manifest.permission.BLUETOOTH_CONNECT,
                android.Manifest.permission.BLUETOOTH_SCAN)
            else arrayOf(android.Manifest.permission.ACCESS_FINE_LOCATION,
                android.Manifest.permission.ACCESS_COARSE_LOCATION)
    }
}
