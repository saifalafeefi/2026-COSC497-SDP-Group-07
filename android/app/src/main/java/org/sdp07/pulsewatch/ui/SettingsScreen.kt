package org.sdp07.pulsewatch.ui

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.widget.Toast
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.sdp07.pulsewatch.BandLink
import org.sdp07.pulsewatch.History
import org.sdp07.pulsewatch.LinkState

@Composable
fun SettingsScreen(
    link: BandLink,
    history: History,
    bandName: String,
    address: String?,
    demo: Boolean,
    version: String,
    alerts: Boolean,
    onAlerts: (Boolean) -> Unit,
    onWifi: () -> Unit,
    onSensitivity: (Float) -> Unit,
    onForget: () -> Unit,
    onExportPdf: () -> Unit,
    onExportCsv: () -> Unit,
) {
    val ctx = LocalContext.current
    val state by link.state.collectAsStateWithLifecycle()
    val st by link.status.collectAsStateWithLifecycle()
    val net by link.net.collectAsStateWithLifecycle()
    val hv by history.version.collectAsStateWithLifecycle()
    var stored by remember { mutableStateOf(0 to 0) }
    LaunchedEffect(hv) { stored = withContext(Dispatchers.IO) { history.size() } }
    var confirmDelete by remember { mutableStateOf(false) }
    var confirmForget by remember { mutableStateOf(false) }
    val connected = state == LinkState.Connected

    Column(
        Modifier.fillMaxSize().statusBarsPadding().verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        Text("Settings", color = Pw.text, fontSize = 20.sp, fontWeight = FontWeight.SemiBold,
            modifier = Modifier.padding(start = 4.dp))

        // ---- the band
        Section("Band") {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(bandName, color = Pw.text, fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
                    Text(if (demo) "Simulated, no hardware" else (address ?: ""), color = Pw.faint, fontSize = 12.sp,
                        fontFamily = FontFamily.Monospace)
                }
                when {
                    demo -> Pill("Demo", Pw.master)
                    connected -> Pill("Connected", Pw.calm)
                    state == LinkState.Connecting -> Pill("Connecting", Pw.dim, pulsing = true)
                    else -> Pill("Out of reach", Pw.near)
                }
            }
            val inf by link.info.collectAsStateWithLifecycle()
            if (!st?.subject.isNullOrBlank()) Line("Wearer", st!!.subject)
            when (inf?.calibrated) {
                1 -> Line("Calibration", "kept on the band")
                0 -> Line("Calibration", "not calibrated")
            }
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                OutlinedButton(onClick = { link.send("id=5") }, enabled = connected && !demo,
                    shape = RoundedCornerShape(12.dp)) { Text("Flash its light") }
                OutlinedButton(onClick = { confirmForget = true }, shape = RoundedCornerShape(12.dp)) {
                    Text(if (demo) "Leave the demo" else "Forget band", color = Pw.alert)
                }
            }
        }

        // ---- WiFi: where the band is, and the IP the master needs
        Section("WiFi") {
            val n = net
            when {
                n == null -> Text(if (connected) "This band's firmware doesn't report its network yet."
                                  else "Connect to the band to see its network.", color = Pw.dim, fontSize = 14.sp)
                n.state == "on" -> {
                    Line("Network", n.ssid)
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text("IP address", color = Pw.dim, fontSize = 14.sp, modifier = Modifier.weight(1f))
                        SelectionContainer {
                            Text(n.ip, color = Pw.text, fontSize = 15.sp, fontFamily = FontFamily.Monospace)
                        }
                        Spacer(Modifier.width(6.dp))
                        TextButton(onClick = { copy(ctx, n.ip) }) { Text("Copy", color = Pw.calm) }
                    }
                    Line("Signal", "${n.rssi} dBm · " + when {
                        n.rssi >= -55 -> "strong"; n.rssi >= -70 -> "good"; n.rssi >= -80 -> "weak"; else -> "very weak"
                    })
                    Text("To add this band to Pulse Fleet, give the master this IP (Pulse Control → Board setup).",
                        color = Pw.faint, fontSize = 12.sp, lineHeight = 17.sp)
                }
                n.saved > 0 -> Text("Looking for a saved network (${n.saved} saved), not connected right now.",
                    color = Pw.near, fontSize = 14.sp)
                else -> Text("Not on any WiFi network yet.", color = Pw.near, fontSize = 14.sp)
            }
            Button(onClick = onWifi, enabled = connected, shape = RoundedCornerShape(12.dp),
                colors = ButtonDefaults.buttonColors(containerColor = Pw.panelHi, contentColor = Pw.text)) {
                Text(if (net?.state == "on") "Change network" else "Set up WiFi")
            }
        }

        // ---- detection
        Section("Detection") {
            var sens by remember { mutableFloatStateOf(st?.sensitivity ?: 0.5f) }
            var touched by remember { mutableStateOf(false) }
            LaunchedEffect(st?.sensitivity) { if (!touched) sens = st?.sensitivity ?: sens }
            Row(Modifier.fillMaxWidth()) {
                Text("Sensitivity", color = Pw.text, fontSize = 14.sp, modifier = Modifier.weight(1f))
                Text("${(sens * 100).toInt()}", color = Pw.dim, fontSize = 14.sp)
            }
            Slider(value = sens, onValueChange = { sens = it; touched = true },
                onValueChangeFinished = { onSensitivity(sens); touched = false }, enabled = connected,
                colors = SliderDefaults.colors(thumbColor = Pw.calm, activeTrackColor = Pw.calm,
                    inactiveTrackColor = Pw.panelHi))
            Row(Modifier.fillMaxWidth()) {
                Text("Fewer alerts", color = Pw.faint, fontSize = 12.sp, modifier = Modifier.weight(1f))
                Text("Catch more", color = Pw.faint, fontSize = 12.sp)
            }
            HorizontalDivider(color = Pw.line)
            Text("Calibration", color = Pw.text, fontSize = 14.sp, fontWeight = FontWeight.Medium)
            Text("Calibrating to a wearer is done from the master only: the Calibrate button on Pulse " +
                    "Fleet's roster. The phone can't start one.", color = Pw.dim, fontSize = 13.sp, lineHeight = 18.sp)
            Text("\"Settling in\" at the start of each wear is not a calibration. The band compares you with " +
                    "your own last few minutes, so it needs a few of them first. That reference is kept only " +
                    "in the band's memory and is dropped when the band comes off.",
                color = Pw.faint, fontSize = 12.sp, lineHeight = 17.sp)
        }

        // ---- alerts
        Section("Alerts") {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text("Notify me when the band flags", color = Pw.text, fontSize = 14.sp)
                    Text("A notification each time a flag starts", color = Pw.faint, fontSize = 12.sp)
                }
                Switch(checked = alerts, onCheckedChange = onAlerts,
                    colors = SwitchDefaults.colors(checkedTrackColor = Pw.calm, checkedThumbColor = Pw.ink))
            }
        }

        // ---- data
        Section("Your data") {
            Text("${stored.second} day${if (stored.second == 1) "" else "s"} · " +
                    "%,d readings stored on this phone".format(stored.first),
                color = Pw.text, fontSize = 14.sp)
            Text("Nothing is uploaded. Exports go wherever you send them.", color = Pw.faint, fontSize = 12.sp)
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                Button(onClick = onExportPdf, enabled = stored.first > 0, shape = RoundedCornerShape(12.dp),
                    colors = ButtonDefaults.buttonColors(containerColor = Pw.calm, contentColor = Pw.ink)) {
                    Text("PDF report", fontWeight = FontWeight.SemiBold)
                }
                OutlinedButton(onClick = onExportCsv, enabled = stored.first > 0, shape = RoundedCornerShape(12.dp)) {
                    Text("Raw data (CSV)")
                }
            }
            TextButton(onClick = { confirmDelete = true }, enabled = stored.first > 0) {
                Text("Delete history", color = if (stored.first > 0) Pw.alert else Pw.faint)
            }
        }

        Section("About") {
            Line("Pulse Watch", version)
            Text("An early-warning aid, not a medical device. Every flag is for a person to review.",
                color = Pw.faint, fontSize = 12.sp, lineHeight = 17.sp)
        }
        Spacer(Modifier.height(4.dp))
    }

    if (confirmDelete) AlertDialog(
        onDismissRequest = { confirmDelete = false },
        title = { Text("Delete history?") },
        text = { Text("Every stored reading and flag on this phone is removed. This can't be undone.") },
        confirmButton = { TextButton(onClick = { history.clear(); confirmDelete = false }) { Text("Delete", color = Pw.alert) } },
        dismissButton = { TextButton(onClick = { confirmDelete = false }) { Text("Keep") } },
        containerColor = Pw.panel, titleContentColor = Pw.text, textContentColor = Pw.dim,
    )
    if (confirmForget) AlertDialog(
        onDismissRequest = { confirmForget = false },
        title = { Text(if (demo) "Leave the demo?" else "Forget this band?") },
        text = { Text(if (demo) "Back to pairing." else "The app stops connecting to $bandName. Its history stays on this phone.") },
        confirmButton = { TextButton(onClick = { confirmForget = false; onForget() }) {
            Text(if (demo) "Leave" else "Forget", color = Pw.alert) } },
        dismissButton = { TextButton(onClick = { confirmForget = false }) { Text("Cancel") } },
        containerColor = Pw.panel, titleContentColor = Pw.text, textContentColor = Pw.dim,
    )
}

@Composable
private fun Section(title: String, content: @Composable () -> Unit) {
    Panel {
        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Eyebrow(title)
            content()
        }
    }
}

@Composable
private fun Line(k: String, v: String) {
    Row(Modifier.fillMaxWidth()) {
        Text(k, color = Pw.dim, fontSize = 14.sp, modifier = Modifier.weight(1f))
        Text(v, color = Pw.text, fontSize = 14.sp)
    }
}

private fun copy(ctx: Context, s: String) {
    ctx.getSystemService(ClipboardManager::class.java)?.setPrimaryClip(ClipData.newPlainText("Band IP", s))
    Toast.makeText(ctx, "IP copied", Toast.LENGTH_SHORT).show()
}
