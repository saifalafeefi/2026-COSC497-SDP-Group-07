package org.sdp07.pulsewatch.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.DateRange
import androidx.compose.material.icons.filled.Favorite
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

private data class Tab(val label: String, val icon: ImageVector)

private val tabs = listOf(
    Tab("Live", Icons.Filled.Favorite),
    Tab("History", Icons.Filled.DateRange),
    Tab("Settings", Icons.Filled.Settings),
)

/** the three sections, with the tab bar along the bottom */
@Composable
fun Shell(selected: Int, onSelect: (Int) -> Unit, content: @Composable () -> Unit) {
    Column(Modifier.fillMaxSize().background(Pw.ink)) {
        Box(Modifier.weight(1f).fillMaxWidth()) { content() }
        HorizontalDivider(color = Pw.line.copy(alpha = 0.6f))
        NavigationBar(containerColor = Pw.ink, tonalElevation = 0.dp) {
            tabs.forEachIndexed { i, t ->
                NavigationBarItem(
                    selected = i == selected, onClick = { onSelect(i) },
                    icon = { Icon(t.icon, t.label) },
                    label = { Text(t.label, fontSize = 12.sp) },
                    colors = NavigationBarItemDefaults.colors(
                        selectedIconColor = Pw.ink, selectedTextColor = Pw.calm,
                        indicatorColor = Pw.calm, unselectedIconColor = Pw.dim, unselectedTextColor = Pw.dim),
                )
            }
        }
    }
}

/** a small floating note while an export is being made */
@Composable
fun ExportingNote(what: String) {
    Box(Modifier.fillMaxSize().navigationBarsPadding().padding(bottom = 96.dp), contentAlignment = Alignment.BottomCenter) {
        Row(
            Modifier.clip(RoundedCornerShape(50)).background(Pw.panelHi).padding(horizontal = 18.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            CircularProgressIndicator(Modifier.size(16.dp), color = Pw.calm, strokeWidth = 2.dp)
            Spacer(Modifier.width(10.dp))
            Text("$what…", color = Pw.text, fontSize = 13.sp)
        }
    }
}
