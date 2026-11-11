package org.sdp07.pulsewatch.ui

import android.os.SystemClock
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Favorite
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.delay
import org.sdp07.pulsewatch.BandInfo
import org.sdp07.pulsewatch.BandLink
import org.sdp07.pulsewatch.BandStatus
import org.sdp07.pulsewatch.FoundBand
import org.sdp07.pulsewatch.LinkState

// ======================================================================= pairing

@Composable
fun PairScreen(
    canPair: Boolean,
    blocker: String?,
    blockerAction: String?,
    onBlocker: () -> Unit,
    onPairSheet: () -> Unit,
    onScan: () -> Unit,
    scanning: Boolean,
    found: List<FoundBand>,
    onPick: (FoundBand) -> Unit,
    message: String?,
    onDemo: () -> Unit,
) {
    Column(
        Modifier.fillMaxSize().statusBarsPadding().navigationBarsPadding()
            .verticalScroll(rememberScrollState()).padding(horizontal = 24.dp, vertical = 28.dp),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Eyebrow("Pulse Watch")
        Spacer(Modifier.height(36.dp))
        SearchRings(Modifier.size(200.dp))
        Spacer(Modifier.height(28.dp))
        Text("Pair your band", style = androidx.compose.material3.MaterialTheme.typography.headlineSmall,
            color = Pw.text)
        Spacer(Modifier.height(10.dp))
        Text("Turn the band on and keep it near your phone. It shows up as \"Pulse\" plus " +
                "six letters, the same name as on the roster.",
            color = Pw.dim, textAlign = TextAlign.Center, fontSize = 14.sp, lineHeight = 20.sp)
        Spacer(Modifier.height(28.dp))
        if (blocker != null) {
            Panel {
                Column {
                    Text(blocker, color = Pw.text, fontSize = 14.sp, lineHeight = 20.sp)
                    if (blockerAction != null) {
                        Spacer(Modifier.height(12.dp))
                        Button(onClick = onBlocker, shape = RoundedCornerShape(14.dp)) { Text(blockerAction) }
                    }
                }
            }
        } else {
            Button(
                onClick = onPairSheet, enabled = canPair,
                modifier = Modifier.fillMaxWidth().height(54.dp),
                shape = RoundedCornerShape(16.dp),
                colors = ButtonDefaults.buttonColors(containerColor = Pw.calm, contentColor = Pw.ink)
            ) { Text("Find my band", fontWeight = FontWeight.SemiBold, fontSize = 16.sp) }
            Spacer(Modifier.height(10.dp))
            TextButton(onClick = onScan, enabled = canPair && !scanning) {
                Text(if (scanning) "Looking for bands nearby…" else "Not showing up? Scan here instead",
                    color = Pw.dim)
            }
        }
        if (message != null) {
            Spacer(Modifier.height(8.dp))
            Text(message, color = Pw.near, fontSize = 13.sp, textAlign = TextAlign.Center)
        }
        TextButton(onClick = onDemo) { Text("No band with you? Try the demo", color = Pw.faint) }
        AnimatedVisibility(found.isNotEmpty()) {
            Column(Modifier.fillMaxWidth().padding(top = 16.dp)) {
                Eyebrow("Bands nearby")
                Spacer(Modifier.height(10.dp))
                found.forEach { b ->
                    Row(
                        Modifier.fillMaxWidth().padding(vertical = 4.dp)
                            .clip(RoundedCornerShape(16.dp))
                            .clickable { onPick(b) }
                            .padding(0.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Panel {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Column(Modifier.weight(1f)) {
                                    Text(b.name, color = Pw.text, fontWeight = FontWeight.SemiBold)
                                    Text(b.address, color = Pw.faint, fontSize = 12.sp)
                                }
                                Text(signalWords(b.rssi), color = Pw.dim, fontSize = 13.sp)
                            }
                        }
                    }
                }
            }
        }
    }
}

private fun signalWords(rssi: Int) = when {
    rssi >= -60 -> "right here"
    rssi >= -75 -> "nearby"
    else -> "far"
}

// ======================================================================= live

private data class Headline(val title: String, val sub: String, val tone: Color)

private fun headline(link: LinkState, s: BandStatus?, info: BandInfo?, stale: Boolean): Headline {
    val why = info?.why.orEmpty()
    val keep = info?.keepS ?: 0
    val kept = if (keep > 0) " Window kept for $keep s." else ""
    val finger = info?.finger ?: s?.finger
    return when {
        link == LinkState.Connecting -> Headline("Connecting…", "Reaching your band over Bluetooth.", Pw.dim)
        link == LinkState.Lost || link == LinkState.Idle ->
            Headline("Band out of reach", "Keep it within a few metres; the app keeps trying.", Pw.near)
        s == null || stale -> Headline("Waiting for the band", "Connected, waiting for its first reading.", Pw.dim)
        finger == "check" -> Headline("Checking it's a finger — ${info?.fingerN ?: 0}/${info?.fingerOf ?: 5} s",
            "Hold still.$kept", Pw.master)
        finger == "unsteady" -> Headline("Pulse not steady yet", "Hold still.$kept", Pw.near)
        finger == "nopulse" -> Headline("No pulse — press lighter",
            "Something is on the sensor. Rest the fingertip lightly.$kept", Pw.near)
        finger == "none" || finger == null -> if (keep > 0)
            Headline("Finger lifted", "Window kept for $keep s — put the finger back to continue.", Pw.near)
            else Headline("Place your fingertip", "Rest it lightly on the sensor window.", Pw.dim)
        // not a calibration: the band compares you with this wear's own last few
        // minutes, and needs a few of them before a level means anything
        // nobody is scoring: the band says why (e.g. its own detector is off)
        s.source == "none" && why.isNotEmpty() ->
            Headline("No verdict", why.replaceFirstChar { it.uppercase() } + ".", Pw.near)
        s.state == "warm" && why.startsWith("settling in") ->
            Headline("Settling in — ${s.waitS} s left",
                "Reading your pulse to compare against (" + why.substringAfter("— ") +
                    "). Not a calibration. Moving pauses it.", Pw.master)
        s.state == "warm" -> Headline(if (s.waitS > 0) "Warming up — ${s.waitS} s left" else "Warming up",
            why.ifEmpty { "Filling the first 60 s window." }.replaceFirstChar { it.uppercase() } + ".", Pw.master)
        s.state == "hold" -> Headline("Signal weak — hold still",
            why.ifEmpty { "Movement in the last minute. Holding the last reading." }
                .replaceFirstChar { it.uppercase() } + ".", Pw.near)
        s.flag -> Headline("Above your calm", "Your heart rate has stayed above your usual. Take a moment.", Pw.alert)
        s.level != null && s.level >= s.threshold * 0.85f ->
            Headline("Getting close", "A little above your usual, still below the line.", Pw.near)
        s.level != null -> Headline("Calm", "In line with your own usual.", Pw.calm)
        why.startsWith("settling in") ->             // the master's verdict, still settling
            Headline("Settling in", "Reading your pulse to compare against (" + why.substringAfter("— ") +
                "). Not a calibration.", Pw.master)
        else -> Headline("Waiting for a reading", "The band hasn't scored this minute yet.", Pw.dim)
    }
}

@Composable
fun LiveScreen(
    link: BandLink,
    bandName: String,
    demo: Boolean = false,
    onWifi: () -> Unit,
) {
    val state by link.state.collectAsStateWithLifecycle()
    val s by link.status.collectAsStateWithLifecycle()
    val inf by link.info.collectAsStateWithLifecycle()
    val hr by link.hrHistory.collectAsStateWithLifecycle()
    val net by link.net.collectAsStateWithLifecycle()
    var now by remember { mutableLongStateOf(SystemClock.uptimeMillis()) }
    LaunchedEffect(Unit) { while (true) { now = SystemClock.uptimeMillis(); delay(1000) } }
    val stale = link.statusAgeS() > 5f && now > 0
    val st = s
    val info = inf
    val h = headline(state, st, info, stale)
    val connected = state == LinkState.Connected && !stale

    Column(
        Modifier.fillMaxSize().statusBarsPadding()
            .verticalScroll(rememberScrollState()).padding(horizontal = 16.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        // ---- header
        Row(Modifier.fillMaxWidth().padding(start = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(bandName, color = Pw.text, fontSize = 20.sp, fontWeight = FontWeight.SemiBold)
                Text(if (st?.subject.isNullOrBlank()) "No wearer assigned" else "Worn by ${st!!.subject}" +
                        when (info?.calibrated) { 1 -> " · calibrated"; 0 -> " · not calibrated"; else -> "" },
                    color = Pw.dim, fontSize = 13.sp)
            }
            when (state) {
                LinkState.Connected -> if (demo) Pill("Demo", Pw.master)
                    else Pill(if (stale) "No data" else "Connected", if (stale) Pw.near else Pw.calm)
                LinkState.Connecting -> Pill("Connecting", Pw.dim, pulsing = true)
                else -> Pill("Searching", Pw.near, pulsing = true)
            }
        }

        // ---- a band that has never joined a network: offer to put it on this phone's
        val nt = net
        if (nt != null && nt.state != "on" && nt.saved == 0 && !demo) {
            Panel {
                Column {
                    Eyebrow("Not on WiFi yet", Pw.near)
                    Spacer(Modifier.height(6.dp))
                    Text("Put the band on your WiFi so the master can find it too. You only do this once.",
                        color = Pw.dim, fontSize = 14.sp, lineHeight = 20.sp)
                    Spacer(Modifier.height(12.dp))
                    Button(onClick = onWifi, shape = RoundedCornerShape(14.dp),
                        colors = ButtonDefaults.buttonColors(containerColor = Pw.calm, contentColor = Pw.ink)) {
                        Text("Set up WiFi", fontWeight = FontWeight.SemiBold)
                    }
                }
            }
        }

        // ---- the verdict
        Panel {
            Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally) {
                val showLevel = connected && st?.finger == "on" && st.level != null
                LevelGauge(if (showLevel) st!!.level else null, st?.threshold ?: 0.66f, st?.flag == true) {
                    val checking = connected && (info?.finger ?: st?.finger) == "check"
                    val waiting = connected && !checking && st?.state == "warm" && (st.waitS) > 0
                    val (big, small) = when {
                        showLevel -> "${((st!!.level ?: 0f) * 100).toInt()}" to "stress level"
                        checking -> "${info?.fingerN ?: 0}/${info?.fingerOf ?: 5}" to "checking finger"
                        waiting -> "${st!!.waitS}s" to (if (info?.why.orEmpty().startsWith("settling"))
                            "settling in" else "warming up")
                        else -> "–" to "stress level"
                    }
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Text(big, style = androidx.compose.material3.MaterialTheme.typography.displayLarge,
                            color = if (showLevel || checking || waiting) h.tone else Pw.faint)
                        Eyebrow(small, Pw.faint)
                    }
                }
                // the arc is open at the bottom: pull the words up into that space
                Spacer(Modifier.height(0.dp))
                Text(h.title, color = h.tone, fontSize = 22.sp, fontWeight = FontWeight.SemiBold,
                    modifier = Modifier.offset(y = (-28).dp))
                Text(h.sub, color = Pw.dim, fontSize = 14.sp, textAlign = TextAlign.Center,
                    modifier = Modifier.offset(y = (-24).dp))
                val note = info?.why.orEmpty()
                if (connected && st?.level != null && st.state != "warm" && st.state != "hold" && note.isNotEmpty()) {
                    Text(note.replaceFirstChar { it.uppercase() }, color = Pw.faint, fontSize = 12.sp,
                        textAlign = TextAlign.Center, modifier = Modifier.offset(y = (-16).dp))
                }
                if (connected && st != null && st.source != "none") {
                    Spacer(Modifier.height(0.dp))
                    when (st.source) {
                        "master" -> Pill("Judged by the master", Pw.master)
                        "board" -> Pill("Judged on the band", Pw.calm)
                        "demo" -> Pill("Demo", Pw.dim)
                    }
                }
            }
        }

        // ---- the pulse itself
        Panel {
            Column {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                    Eyebrow("Live pulse")
                    Text("64 Hz · last 6 s · sensor counts", color = Pw.faint, fontSize = 11.sp)
                }
                Spacer(Modifier.height(10.dp))
                PulseWave(link.wave, connected && st?.finger != "none",
                    Modifier.fillMaxWidth().height(130.dp),
                    color = if (st?.flag == true) Pw.alert else Pw.calm)
            }
        }

        // ---- vitals
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            val beat = rememberInfiniteTransition(label = "beat")
            val period = (60_000 / (st?.hr ?: 60).coerceIn(40, 200))
            val k by beat.animateFloat(1f, 1.18f,
                infiniteRepeatable(tween(period / 2), RepeatMode.Reverse), label = "k")
            Vital(Modifier.weight(1f), "Heart", if (connected) st?.hr?.toString() else null, "bpm") {
                Icon(Icons.Filled.Favorite, null, tint = Pw.heart,
                    modifier = Modifier.size(14.dp).scale(if (connected && st?.hr != null) k else 1f))
            }
            Vital(Modifier.weight(1f), "SpO₂", if (connected) st?.spo2?.toString() else null, "%")
            Vital(Modifier.weight(1f), "Motion", if (connected && st?.motion != null)
                (if (st.motion!! >= 0.12f) "Moving" else "Still") else null, "")
        }

        // ---- heart rate, ten minutes
        Panel {
            Column {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                    Eyebrow("Heart rate · 10 min")
                    if (hr.size >= 2) Text("${hr.minOf { it.second }}–${hr.maxOf { it.second }} bpm",
                        color = Pw.faint, fontSize = 11.sp)
                }
                Spacer(Modifier.height(10.dp))
                if (hr.size < 2) Text("Builds up while the band reads your pulse.", color = Pw.faint, fontSize = 13.sp)
                else HrTrend(hr, now, Modifier.fillMaxWidth().height(70.dp))
            }
        }

        // ---- what the band works out itself
        Panel {
            Column(verticalArrangement = Arrangement.spacedBy(14.dp)) {
                Eyebrow("On the band itself")
                LevelBar("Heart-rate detector", wordsFor(st?.bandState, null),
                    if (connected) st?.bandLevel else null, st?.threshold ?: 0.66f, st?.bandFlag == true)
                LevelBar("AI model", wordsFor(st?.aiState, st?.aiMs),
                    if (connected) st?.aiLevel else null, st?.threshold ?: 0.66f, st?.aiFlag == true)
                Text("Both run on the band's own chip. The headline above is whichever the band " +
                        "trusts right now: the master's when it's connected, otherwise its own.",
                    color = Pw.faint, fontSize = 12.sp, lineHeight = 17.sp)
            }
        }

        Spacer(Modifier.height(4.dp))
    }
}

private fun wordsFor(st: String?, ms: Int?): String = when (st) {
    null -> "–"
    "ok" -> if (ms != null && ms > 0) "$ms ms a run" else "running"
    "warm" -> "settling in"
    "wait" -> "waiting for a clean minute"
    "hold" -> "holding: movement"
    "idle" -> "no finger"
    "loading" -> "loading"
    "fail" -> "self-test failed"
    "none" -> "not in this firmware"
    "off" -> "off"
    else -> st
}

@Composable
private fun Vital(modifier: Modifier, label: String, value: String?, unit: String,
                  icon: (@Composable () -> Unit)? = null) {
    Box(modifier) {
        Panel {
            Column {
                Eyebrow(label)
                Spacer(Modifier.height(8.dp))
                Row(verticalAlignment = Alignment.Bottom) {
                    Text(value ?: "–", color = if (value == null) Pw.faint else Pw.text,
                        fontSize = 26.sp, fontWeight = FontWeight.SemiBold)
                    if (icon != null) {
                        Spacer(Modifier.width(5.dp))
                        Box(Modifier.padding(bottom = 9.dp)) { icon() }
                    }
                    if (value != null && unit.isNotEmpty()) {
                        Spacer(Modifier.width(4.dp))
                        Text(unit, color = Pw.dim, fontSize = 13.sp, modifier = Modifier.padding(bottom = 4.dp))
                    }
                }
            }
        }
    }
}
