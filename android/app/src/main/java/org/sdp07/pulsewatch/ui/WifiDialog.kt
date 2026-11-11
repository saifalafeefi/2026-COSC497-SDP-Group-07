package org.sdp07.pulsewatch.ui

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
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import org.sdp07.pulsewatch.BandLink
import org.sdp07.pulsewatch.PhoneWifi

/**
 * Put the band on a WiFi network from the phone: the network name (filled in
 * from the phone's own, if Android lets us read it), a password, and progress
 * through pairing, sending and the band actually joining.
 */
@Composable
fun WifiDialog(
    link: BandLink,
    bandName: String,
    /** read the phone's network; ask = may request the location permission */
    onUsePhone: (ask: Boolean, done: (PhoneWifi?) -> Unit) -> Unit,
    onClose: () -> Unit,
) {
    val step by link.wifiStep.collectAsStateWithLifecycle()
    val net by link.net.collectAsStateWithLifecycle()
    var ssid by remember { mutableStateOf("") }
    var pass by remember { mutableStateOf("") }
    var show by remember { mutableStateOf(false) }
    var note by remember { mutableStateOf<String?>(null) }
    var sentSsid by remember { mutableStateOf<String?>(null) }
    // try once on open: if the permission is already there, this fills the name in
    LaunchedEffect(Unit) {
        link.clearWifiStep()
        onUsePhone(false) { w -> if (w != null && ssid.isEmpty()) { ssid = w.ssid; if (w.fiveGhz) note = FIVE_GHZ } }
    }
    val joined = sentSsid != null && net?.joinResult == "ok" && net?.ssid == sentSsid
    val failed = sentSsid != null && net?.joinSsid == sentSsid && net?.joinResult == "fail"
    val busy = !joined && !failed && (step == "pairing" || step == "sending" || (step == "sent" && sentSsid != null))
    val err = step?.takeIf { it.startsWith("error:") }?.removePrefix("error:")

    Dialog(onDismissRequest = { if (!busy) onClose() }) {
        Box(Modifier.fillMaxWidth().clip(RoundedCornerShape(24.dp)).background(Pw.panel)
            .border(1.dp, Pw.line, RoundedCornerShape(24.dp)).padding(22.dp)) {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Eyebrow("WiFi for $bandName")
                if (joined) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Box(Modifier.size(12.dp).clip(CircleShape).background(Pw.calm))
                        Spacer(Modifier.width(10.dp))
                        Text("On ${net!!.ssid}", color = Pw.text, fontSize = 20.sp, fontWeight = FontWeight.SemiBold)
                    }
                    Text("The band joined and will reconnect by itself from now on.", color = Pw.dim, fontSize = 14.sp)
                    Panel {
                        Column {
                            Eyebrow("Its IP address")
                            Spacer(Modifier.height(4.dp))
                            Text(net!!.ip, color = Pw.text, fontSize = 22.sp, fontFamily = FontFamily.Monospace)
                            Text("Give this to the master to add the band to Pulse Fleet.",
                                color = Pw.faint, fontSize = 12.sp)
                        }
                    }
                    Button(onClick = onClose, modifier = Modifier.fillMaxWidth().height(50.dp),
                        shape = RoundedCornerShape(14.dp),
                        colors = ButtonDefaults.buttonColors(containerColor = Pw.calm, contentColor = Pw.ink)) {
                        Text("Done", fontWeight = FontWeight.SemiBold)
                    }
                    return@Column
                }

                Text("Put the band on WiFi", color = Pw.text, fontSize = 20.sp, fontWeight = FontWeight.SemiBold)
                Text("Usually the network this phone is on. The band works on 2.4 GHz WiFi only.",
                    color = Pw.dim, fontSize = 14.sp, lineHeight = 20.sp)
                val colors = OutlinedTextFieldDefaults.colors(
                    focusedBorderColor = Pw.calm, unfocusedBorderColor = Pw.line, focusedLabelColor = Pw.calm,
                    unfocusedLabelColor = Pw.dim, focusedTextColor = Pw.text, unfocusedTextColor = Pw.text,
                    cursorColor = Pw.calm)
                OutlinedTextField(value = ssid, onValueChange = { ssid = it.take(32) }, singleLine = true,
                    label = { Text("Network name") }, enabled = !busy, colors = colors,
                    modifier = Modifier.fillMaxWidth())
                TextButton(onClick = {
                    note = null
                    onUsePhone(true) { w ->
                        if (w == null) note = "Couldn't read this phone's network. Allow location and switch " +
                                "location on, or type the name."
                        else { ssid = w.ssid; note = if (w.fiveGhz) FIVE_GHZ else null }
                    }
                }, enabled = !busy) { Text("Use this phone's network", color = Pw.calm) }
                OutlinedTextField(value = pass, onValueChange = { pass = it.take(64) }, singleLine = true,
                    label = { Text("Password") }, enabled = !busy, colors = colors,
                    visualTransformation = if (show) VisualTransformation.None else PasswordVisualTransformation(),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                    trailingIcon = { TextButton(onClick = { show = !show }) {
                        Text(if (show) "Hide" else "Show", color = Pw.dim, fontSize = 12.sp) } },
                    modifier = Modifier.fillMaxWidth())
                if (note != null) Text(note!!, color = Pw.near, fontSize = 13.sp, lineHeight = 18.sp)

                // progress
                when {
                    err != null -> Text(err, color = Pw.alert, fontSize = 13.sp)
                    failed -> Text("The band couldn't join ${sentSsid}. Check the password, and that the " +
                            "network is 2.4 GHz.", color = Pw.alert, fontSize = 13.sp, lineHeight = 18.sp)
                    step == "pairing" -> Busy("Confirm the pairing request on your phone. It keeps the password encrypted.")
                    step == "sending" -> Busy("Sending to the band…")
                    busy -> Busy("The band is joining ${sentSsid}…")
                }

                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
                    TextButton(onClick = onClose, enabled = !busy) { Text("Cancel", color = Pw.dim) }
                    Spacer(Modifier.width(8.dp))
                    Button(onClick = {
                        note = null
                        sentSsid = ssid.trim()
                        link.sendWifi(ssid.trim(), pass)
                    }, enabled = !busy && ssid.isNotBlank() && (pass.isEmpty() || pass.length >= 8),
                        shape = RoundedCornerShape(14.dp),
                        colors = ButtonDefaults.buttonColors(containerColor = Pw.calm, contentColor = Pw.ink)) {
                        Text(if (failed || err != null) "Try again" else "Send to band", fontWeight = FontWeight.SemiBold)
                    }
                }
            }
        }
    }
}

private const val FIVE_GHZ = "This phone is on 5 GHz. If the router has a separate 2.4 GHz network, " +
        "use that name instead."

@Composable
private fun Busy(text: String) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        CircularProgressIndicator(Modifier.size(18.dp), color = Pw.calm, strokeWidth = 2.dp)
        Spacer(Modifier.width(12.dp))
        Text(text, color = Pw.dim, fontSize = 13.sp, lineHeight = 18.sp)
    }
}
