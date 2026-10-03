package app.cloudlink

import android.Manifest
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.OpenableColumns
import android.provider.Settings
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.ContentCopy
import androidx.compose.material.icons.filled.Devices
import androidx.compose.material.icons.filled.Download
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.FolderOpen
import androidx.compose.material.icons.filled.Image
import androidx.compose.material.icons.filled.InsertDriveFile
import androidx.compose.material.icons.filled.Link
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.Movie
import androidx.compose.material.icons.filled.MusicNote
import androidx.compose.material.icons.filled.PhoneAndroid
import androidx.compose.material.icons.filled.Computer
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.SwapVert
import androidx.compose.material.icons.filled.UploadFile
import androidx.compose.material.icons.filled.Wifi
import androidx.compose.material.icons.filled.WifiOff
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.text.DateFormat
import java.util.Date

class MainActivity : ComponentActivity() {
    private val engine get() = (application as CloudLinkApp).engine

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        ServerService.start(this)   // keeps serving in the background
        setContent { CloudLinkTheme { CloudLinkUi(engine) } }
    }
}

// ------------------------------------------------------------------------------------------------ theme

private val Lime = Color(0xFFE4FF7A)
private val PastelYellow = Color(0xFFFFF09A)
private val PastelPurple = Color(0xFFD4B0F5)
private val PastelOrange = Color(0xFFFFBE98)
private val Ink = Color(0xFF16181A)

/** Dark or light — always follows the device setting. */
@Composable
private fun CloudLinkTheme(content: @Composable () -> Unit) {
    val scheme = if (isSystemInDarkTheme()) darkColorScheme(
        primary = Lime, onPrimary = Ink, secondary = PastelPurple, onSecondary = Ink,
        background = Color(0xFF0F0F10), onBackground = Color(0xFFF2F2F3),
        surface = Color(0xFF1C1C1F), onSurface = Color(0xFFF2F2F3),
        surfaceVariant = Color(0xFF2A2A2E), onSurfaceVariant = Color(0xFFA8A8B0),
    ) else lightColorScheme(
        primary = Color(0xFFCFEF52), onPrimary = Ink, secondary = Color(0xFF9B6BE0), onSecondary = Color.White,
        background = Color(0xFFF4F5F0), onBackground = Color(0xFF17181A),
        surface = Color.White, onSurface = Color(0xFF17181A),
        surfaceVariant = Color(0xFFE9EBE3), onSurfaceVariant = Color(0xFF5E6066),
    )
    MaterialTheme(colorScheme = scheme, content = content)
}

@Composable
private fun accentText(): Color = if (isSystemInDarkTheme()) Lime else Color(0xFF4F5F00)

@Composable
private fun Panel(modifier: Modifier = Modifier, content: @Composable ColumnScope.() -> Unit) {
    Surface(modifier.fillMaxWidth(), shape = RoundedCornerShape(24.dp), color = MaterialTheme.colorScheme.surface) {
        Column(Modifier.padding(16.dp), content = content)
    }
}

@Composable
private fun Tile(label: String, value: String, color: Color, icon: ImageVector, modifier: Modifier = Modifier) {
    Column(modifier.clip(RoundedCornerShape(24.dp)).background(color).padding(16.dp)) {
        Icon(icon, null, tint = Ink)
        Spacer(Modifier.height(18.dp))
        Text(label, color = Ink, fontSize = 13.sp)
        Text(value, color = Ink, fontSize = 28.sp, fontWeight = FontWeight.Bold)
    }
}

@Composable
private fun Title(text: String, modifier: Modifier = Modifier) =
    Text(text, modifier, fontSize = 22.sp, fontWeight = FontWeight.Bold, color = MaterialTheme.colorScheme.onBackground)

@Composable
private fun Dim(text: String, modifier: Modifier = Modifier, mono: Boolean = false) =
    Text(text, modifier, fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
        fontFamily = if (mono) FontFamily.Monospace else null)

@Composable
private fun LimeButton(text: String, modifier: Modifier = Modifier, onClick: () -> Unit) =
    Button(onClick, modifier, shape = CircleShape,
        colors = ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.primary, contentColor = Ink)) {
        Text(text, fontWeight = FontWeight.SemiBold)
    }

private enum class Tab(val label: String, val icon: ImageVector) {
    Devices("Devices", Icons.Filled.Devices), Files("Files", Icons.Filled.Folder),
    Transfers("Transfers", Icons.Filled.SwapVert), Settings("Settings", Icons.Filled.Settings)
}

@Composable
private fun FloatingNav(tab: Tab, busy: Boolean, onTab: (Tab) -> Unit, modifier: Modifier = Modifier) {
    Surface(modifier, shape = RoundedCornerShape(36.dp), color = MaterialTheme.colorScheme.surfaceVariant, shadowElevation = 10.dp) {
        Row(Modifier.padding(8.dp), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            for (t in Tab.entries) {
                val sel = t == tab
                Box(
                    Modifier.size(52.dp).clip(CircleShape).background(if (sel) Lime else Color.Transparent).clickable { onTab(t) },
                    contentAlignment = Alignment.Center,
                ) {
                    Icon(t.icon, t.label, tint = if (sel) Ink else MaterialTheme.colorScheme.onSurfaceVariant)
                    if (t == Tab.Transfers && busy && !sel) {
                        Box(Modifier.align(Alignment.TopEnd).padding(10.dp).size(8.dp).clip(CircleShape).background(PastelOrange))
                    }
                }
            }
        }
    }
}

@Composable
private fun CloudLinkUi(engine: Engine) {
    val ctx = LocalContext.current
    var tab by remember { mutableStateOf(Tab.Devices) }
    var browsing by remember { mutableStateOf<PeerInfo?>(null) }
    val pairing by engine.pairing.collectAsStateWithLifecycle()
    val transfers by engine.transfers.collectAsStateWithLifecycle()

    val notifPerm = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {}
    LaunchedEffect(Unit) {
        if (Build.VERSION.SDK_INT >= 33) notifPerm.launch(Manifest.permission.POST_NOTIFICATIONS)
        engine.toasts.collect { Toast.makeText(ctx, it, Toast.LENGTH_LONG).show() }
    }
    LaunchedEffect(Unit) {
        engine.rejected.collect { Toast.makeText(ctx, "This device no longer trusts you — pair again", Toast.LENGTH_LONG).show() }
    }

    PairingDialog(engine, pairing)

    Box(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background).statusBarsPadding()) {
        when (tab) {
            Tab.Devices -> DevicesScreen(engine, onOpen = { browsing = it; tab = Tab.Files })
            Tab.Files -> FilesScreen(engine, browsing, onPickDevice = { tab = Tab.Devices })
            Tab.Transfers -> TransfersScreen(engine)
            Tab.Settings -> SettingsScreen(engine)
        }
        FloatingNav(tab, transfers.values.any { !it.finished }, { tab = it },
            Modifier.align(Alignment.BottomCenter).navigationBarsPadding().padding(bottom = 12.dp))
    }
}

private val ScreenPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 12.dp, bottom = 104.dp)

// ------------------------------------------------------------------------------------------------ pairing

@Composable
private fun PairingDialog(engine: Engine, p: Pairing) {
    when (p) {
        Pairing.Idle -> {}
        is Pairing.Connecting -> AlertDialog(
            onDismissRequest = {}, title = { Text("Pairing") },
            text = { Row(verticalAlignment = Alignment.CenterVertically) {
                CircularProgressIndicator(Modifier.size(24.dp)); Spacer(Modifier.size(12.dp))
                Text("Waiting for the other device to accept…") } },
            confirmButton = { TextButton({ engine.cancelPairing() }) { Text("Cancel") } },
        )
        is Pairing.Request -> AlertDialog(
            onDismissRequest = {}, title = { Text("Pairing request") },
            text = { Text("\"${p.peerName}\" (${p.host}) wants to pair with this phone. Only accept if you started it.") },
            confirmButton = { Button({ engine.answerPairing(true) }) { Text("Accept") } },
            dismissButton = { TextButton({ engine.answerPairing(false) }) { Text("Decline") } },
        )
        is Pairing.Verify -> AlertDialog(
            onDismissRequest = {}, title = { Text("Compare the code") },
            text = { Column {
                Text("Pairing with \"${p.peerName}\". The SAME 6 digits must appear on both devices:")
                Text(p.sas.chunked(3).joinToString(" "), fontSize = 40.sp, fontFamily = FontFamily.Monospace,
                    modifier = Modifier.padding(vertical = 16.dp))
                Text("If they differ, someone may be intercepting — press \"Different\".")
            } },
            confirmButton = { Button({ engine.answerPairing(true) }) { Text("Same") } },
            dismissButton = { TextButton({ engine.answerPairing(false) }) { Text("Different") } },
        )
        is Pairing.Done -> AlertDialog(
            onDismissRequest = { engine.dismissPairing() }, title = { Text("Paired") },
            text = { Text("\"${p.peerName}\" is now a trusted device.") },
            confirmButton = { TextButton({ engine.dismissPairing() }) { Text("OK") } },
        )
        is Pairing.Failed -> AlertDialog(
            onDismissRequest = { engine.dismissPairing() }, title = { Text("Pairing failed") },
            text = { Text(when (p.reason) {
                "declined" -> "The pairing was declined or timed out."
                "sas_mismatch" -> "The confirmation did not match."
                "commit_mismatch" -> "The other device failed the key check. Try again."
                "busy" -> "The other device is already pairing."
                else -> p.reason
            }) },
            confirmButton = { TextButton({ engine.dismissPairing() }) { Text("OK") } },
        )
    }
}

// ------------------------------------------------------------------------------------------------ devices

@Composable
private fun DevicesScreen(engine: Engine, onOpen: (PeerInfo) -> Unit) {
    val ctx = LocalContext.current
    val clipboard = LocalClipboardManager.current
    val peers by engine.peers.collectAsStateWithLifecycle()
    val transfers by engine.transfers.collectAsStateWithLifecycle()
    val ips = remember(peers) { localIpv4s() }
    var host by remember { mutableStateOf("") }
    var portText by remember { mutableStateOf("47616") }
    var confirmUnpair by remember { mutableStateOf<PeerInfo?>(null) }
    val address = (ips.firstOrNull() ?: "no Wi-Fi IP") + ":" + engine.port

    confirmUnpair?.let { peer ->
        AlertDialog(
            onDismissRequest = { confirmUnpair = null }, title = { Text("Forget ${peer.name}?") },
            text = { Text("It will lose access to this phone's files. Pair again to restore it.") },
            confirmButton = { Button({ engine.unpair(peer.id); confirmUnpair = null }) { Text("Forget") } },
            dismissButton = { TextButton({ confirmUnpair = null }) { Text("Cancel") } },
        )
    }

    LazyColumn(Modifier.fillMaxSize(), contentPadding = ScreenPadding, verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(34.dp).clip(RoundedCornerShape(10.dp)).background(Lime), contentAlignment = Alignment.Center) {
                    Icon(Icons.Filled.Link, null, tint = Ink)
                }
                Text("CloudLink", Modifier.padding(start = 10.dp), fontWeight = FontWeight.Bold, fontSize = 18.sp,
                    color = MaterialTheme.colorScheme.onBackground)
            }
            Text("Hello,", Modifier.padding(top = 18.dp), fontSize = 24.sp, fontWeight = FontWeight.Bold,
                color = accentText())
            Text(engine.state.deviceName, fontSize = 24.sp, fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.onBackground)
            if (!CloudLinkApp.hasAllFilesAccess()) AllFilesBanner()
        }
        item {
            Panel {
                Dim("Your address")
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(address, Modifier.weight(1f), fontFamily = FontFamily.Monospace, fontSize = 22.sp,
                        fontWeight = FontWeight.Bold, color = MaterialTheme.colorScheme.onSurface)
                    IconButton({
                        clipboard.setText(AnnotatedString(address))
                        Toast.makeText(ctx, "Address copied", Toast.LENGTH_SHORT).show()
                    }) { Icon(Icons.Filled.ContentCopy, "Copy address", tint = MaterialTheme.colorScheme.onSurfaceVariant) }
                }
                Dim(if (ips.size > 1) "Other addresses: ${ips.drop(1).joinToString()}" else
                    "If the other device can't find this phone, type this address there.")
            }
        }
        item {
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Tile("Online", peers.count { it.online }.toString(), Lime, Icons.Filled.Wifi, Modifier.weight(1f))
                Tile("Paired", peers.count { it.paired }.toString(), PastelYellow, Icons.Filled.Devices, Modifier.weight(1f))
            }
        }
        item {
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Tile("Transfers", transfers.values.count { !it.finished }.toString(), PastelPurple, Icons.Filled.SwapVert, Modifier.weight(1f))
                Tile("Network", if (ips.isEmpty()) "No Wi-Fi" else "Wi-Fi", PastelOrange,
                    if (ips.isEmpty()) Icons.Filled.WifiOff else Icons.Filled.Wifi, Modifier.weight(1f))
            }
        }
        item { Title("Nearby devices", Modifier.padding(top = 8.dp)) }
        if (peers.isEmpty()) item {
            Panel {
                Text("No devices found yet", fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurface)
                Dim("Open CloudLink on the other device (same Wi-Fi), or connect by IP below.")
            }
        }
        items(peers, key = { it.id }) { p ->
            Surface(
                Modifier.fillMaxWidth().clickable(enabled = p.paired) { onOpen(p) },
                shape = RoundedCornerShape(24.dp), color = MaterialTheme.colorScheme.surface,
            ) {
                Row(Modifier.padding(14.dp), verticalAlignment = Alignment.CenterVertically) {
                    Box(Modifier.size(44.dp).clip(RoundedCornerShape(14.dp)).background(if (p.online) Lime else MaterialTheme.colorScheme.surfaceVariant),
                        contentAlignment = Alignment.Center) {
                        val isPc = p.name.contains("linux", true) || p.name.contains("pc", true)
                        Icon(if (isPc) Icons.Filled.Computer else Icons.Filled.PhoneAndroid, null,
                            tint = if (p.online) Ink else MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    Column(Modifier.weight(1f).padding(start = 12.dp)) {
                        Text(p.name.ifBlank { p.id }, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurface)
                        Dim("${if (p.online) "online" else "offline"} · ${p.host}:${p.port}", mono = true)
                    }
                    if (p.paired) {
                        TextButton({ confirmUnpair = p }) { Text("Forget", color = MaterialTheme.colorScheme.onSurfaceVariant) }
                    } else {
                        LimeButton("Pair") { engine.startPairing(p.host, p.port) }
                    }
                }
            }
        }
        item {
            Panel(Modifier.padding(top = 8.dp)) {
                Text("Connect by IP", fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurface)
                Dim("Use this if your router blocks discovery. The other device shows its address on its Devices screen.")
                Row(Modifier.padding(top = 10.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedTextField(host, { host = it.trim() }, label = { Text("IP address") }, singleLine = true,
                        modifier = Modifier.weight(1f), shape = RoundedCornerShape(16.dp),
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri))
                    OutlinedTextField(portText, { portText = it.filter(Char::isDigit).take(5) }, label = { Text("Port") },
                        singleLine = true, modifier = Modifier.weight(0.55f), shape = RoundedCornerShape(16.dp),
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number))
                }
                LimeButton("Pair", Modifier.padding(top = 10.dp)) {
                    val port = portText.toIntOrNull()
                    if (host.isEmpty() || port == null || port !in 1..65535) {
                        Toast.makeText(ctx, "Enter a valid IP and port", Toast.LENGTH_SHORT).show()
                    } else engine.startPairing(host, port)
                }
            }
        }
    }
}

@Composable
private fun AllFilesBanner() {
    val ctx = LocalContext.current
    Surface(Modifier.fillMaxWidth().padding(top = 14.dp), shape = RoundedCornerShape(24.dp), color = PastelOrange) {
        Column(Modifier.padding(16.dp)) {
            Text("\"All files access\" is off", fontWeight = FontWeight.Bold, color = Ink)
            Text("Paired devices can't browse your storage and downloads may fail until you allow it.", fontSize = 13.sp, color = Ink)
            Button({ openAllFilesSettings(ctx) }, Modifier.padding(top = 8.dp),
                colors = ButtonDefaults.buttonColors(containerColor = Ink, contentColor = Color.White)) { Text("Allow") }
        }
    }
}

private fun openAllFilesSettings(ctx: android.content.Context) {
    if (Build.VERSION.SDK_INT >= 30) {
        val i = Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION, Uri.parse("package:${ctx.packageName}"))
        try { ctx.startActivity(i) } catch (_: Exception) {
            ctx.startActivity(Intent(Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION))
        }
    }
}

// ------------------------------------------------------------------------------------------------ files

private fun humanSize(n: Long): String {
    if (n < 1024) return "$n B"
    val u = arrayOf("KB", "MB", "GB", "TB")
    var v = n.toDouble() / 1024; var i = 0
    while (v >= 1024 && i < u.lastIndex) { v /= 1024; i++ }
    return "%.1f %s".format(v, u[i])
}

private enum class FileFilter(val label: String) { All("All"), Folders("Folders"), Files("Files") }

private fun fileIcon(name: String): ImageVector = when (name.substringAfterLast('.', "").lowercase()) {
    "jpg", "jpeg", "png", "gif", "webp", "heic", "bmp" -> Icons.Filled.Image
    "mp4", "mkv", "mov", "avi", "webm" -> Icons.Filled.Movie
    "mp3", "m4a", "wav", "flac", "ogg", "opus" -> Icons.Filled.MusicNote
    else -> Icons.Filled.InsertDriveFile
}

@Composable
private fun FilesScreen(engine: Engine, peer: PeerInfo?, onPickDevice: () -> Unit) {
    if (peer == null) {
        Column(Modifier.fillMaxSize().padding(24.dp), verticalArrangement = Arrangement.Center, horizontalAlignment = Alignment.CenterHorizontally) {
            Box(Modifier.size(84.dp).clip(CircleShape).background(MaterialTheme.colorScheme.surfaceVariant), contentAlignment = Alignment.Center) {
                Icon(Icons.Filled.FolderOpen, null, Modifier.size(36.dp), tint = accentText())
            }
            Text("Pick a paired device first", Modifier.padding(top = 16.dp), fontWeight = FontWeight.Bold, fontSize = 18.sp,
                color = MaterialTheme.colorScheme.onBackground)
            LimeButton("Devices", Modifier.padding(top = 12.dp), onPickDevice)
        }
        return
    }
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var path by remember(peer.id) { mutableStateOf("/") }
    var entries by remember(peer.id) { mutableStateOf<List<Entry>?>(null) }
    var error by remember(peer.id) { mutableStateOf<String?>(null) }
    var reload by remember(peer.id) { mutableStateOf(0) }
    var filter by remember(peer.id) { mutableStateOf(FileFilter.All) }
    var query by remember(peer.id) { mutableStateOf("") }

    LaunchedEffect(peer.id, path, reload) {
        entries = null; error = null
        try { entries = engine.listDir(peer.id, path) } catch (e: Exception) { error = e.message ?: e.javaClass.simpleName }
    }

    val picker = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri != null) scope.launch {
            val f = withContext(Dispatchers.IO) { copyToCache(ctx, uri) }
            if (f == null) { Toast.makeText(ctx, "Couldn't read that file", Toast.LENGTH_SHORT).show(); return@launch }
            try { engine.upload(peer.id, f, path); reload++ } catch (e: Exception) {
                Toast.makeText(ctx, e.message ?: "Upload failed", Toast.LENGTH_LONG).show()
            } finally { f.delete() }
        }
    }

    fun download(e: Entry) {
        scope.launch {
            try {
                engine.download(peer.id, (if (path == "/") "" else path) + "/" + e.name)
                Toast.makeText(ctx, "Downloading ${e.name}…", Toast.LENGTH_SHORT).show()
            } catch (ex: Exception) { Toast.makeText(ctx, ex.message ?: "Failed", Toast.LENGTH_LONG).show() }
        }
    }

    Column(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
        Row(Modifier.padding(top = 12.dp), verticalAlignment = Alignment.CenterVertically) {
            IconButton(enabled = path != "/", onClick = { path = path.trimEnd('/').substringBeforeLast('/', "").ifEmpty { "/" } },
                modifier = Modifier.clip(CircleShape).background(MaterialTheme.colorScheme.surfaceVariant)) {
                Icon(Icons.Filled.ArrowBack, "Up", tint = MaterialTheme.colorScheme.onSurface)
            }
            Column(Modifier.weight(1f).padding(start = 12.dp)) {
                Title(peer.name)
                Dim(path, mono = true)
            }
            IconButton({ picker.launch("*/*") }, Modifier.clip(CircleShape).background(Lime)) { Icon(Icons.Filled.UploadFile, "Upload here", tint = Ink) }
        }
        OutlinedTextField(query, { query = it }, Modifier.fillMaxWidth().padding(top = 12.dp), singleLine = true,
            placeholder = { Text("Search this folder") }, shape = RoundedCornerShape(20.dp),
            leadingIcon = { Icon(Icons.Filled.Search, null) })
        Row(Modifier.padding(vertical = 12.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            for (f in FileFilter.entries) {
                val sel = f == filter
                Text(f.label, Modifier.clip(CircleShape).background(if (sel) Lime else MaterialTheme.colorScheme.surfaceVariant)
                    .clickable { filter = f }.padding(horizontal = 16.dp, vertical = 8.dp),
                    color = if (sel) Ink else MaterialTheme.colorScheme.onSurface, fontSize = 13.sp)
            }
        }
        Box(Modifier.fillMaxSize()) {
            val all = entries
            val list = all?.filter { query.isBlank() || it.name.contains(query, true) }
            val folders = if (filter == FileFilter.Files) emptyList<Entry>() else list?.filter { it.isDir }.orEmpty()
            val plain = if (filter == FileFilter.Folders) emptyList<Entry>() else list?.filter { !it.isDir }.orEmpty()
            when {
                error != null -> Column(Modifier.padding(24.dp)) {
                    Text("Couldn't load: $error", color = MaterialTheme.colorScheme.onBackground)
                    LimeButton("Retry", Modifier.padding(top = 8.dp)) { reload++ }
                }
                list == null -> CircularProgressIndicator(Modifier.align(Alignment.Center))
                folders.isEmpty() && plain.isEmpty() -> Column(Modifier.align(Alignment.Center), horizontalAlignment = Alignment.CenterHorizontally) {
                    Box(Modifier.size(84.dp).clip(CircleShape).background(MaterialTheme.colorScheme.surfaceVariant), contentAlignment = Alignment.Center) {
                        Icon(Icons.Filled.FolderOpen, null, Modifier.size(36.dp), tint = accentText())
                    }
                    Text("This folder is empty", Modifier.padding(top = 14.dp), fontWeight = FontWeight.Bold, fontSize = 18.sp,
                        color = MaterialTheme.colorScheme.onBackground)
                }
                else -> LazyColumn(contentPadding = PaddingValues(bottom = 104.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    items(folders.chunked(2), key = { row -> row.first().name }) { row ->
                        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                            for (e in row) {
                                Column(
                                    Modifier.weight(1f).clip(RoundedCornerShape(22.dp)).background(MaterialTheme.colorScheme.surface)
                                        .clickable { path = (if (path == "/") "" else path) + "/" + e.name }.padding(16.dp),
                                ) {
                                    Icon(Icons.Filled.Folder, null, tint = MaterialTheme.colorScheme.onSurfaceVariant)
                                    Spacer(Modifier.height(14.dp))
                                    Text(e.name, color = accentText(), fontWeight = FontWeight.SemiBold, maxLines = 1)
                                }
                            }
                            if (row.size == 1) Spacer(Modifier.weight(1f))
                        }
                    }
                    items(plain, key = { "f:" + it.name }) { e ->
                        var menu by remember { mutableStateOf(false) }
                        Surface(Modifier.fillMaxWidth().clickable { download(e) }, shape = RoundedCornerShape(22.dp), color = MaterialTheme.colorScheme.surface) {
                            Row(Modifier.padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
                                Box(Modifier.size(44.dp).clip(RoundedCornerShape(14.dp)).background(MaterialTheme.colorScheme.surfaceVariant),
                                    contentAlignment = Alignment.Center) {
                                    Icon(fileIcon(e.name), null, tint = MaterialTheme.colorScheme.onSurface)
                                }
                                Column(Modifier.weight(1f).padding(horizontal = 12.dp)) {
                                    Text(e.name, maxLines = 1, color = MaterialTheme.colorScheme.onSurface)
                                    Dim(DateFormat.getDateInstance(DateFormat.SHORT).format(Date(e.mtime)) + "  •  " + humanSize(e.size))
                                }
                                Box {
                                    IconButton({ menu = true }) { Icon(Icons.Filled.MoreVert, "More", tint = MaterialTheme.colorScheme.onSurfaceVariant) }
                                    DropdownMenu(menu, { menu = false }) {
                                        DropdownMenuItem(text = { Text("Save to this phone") }, leadingIcon = { Icon(Icons.Filled.Download, null) },
                                            onClick = { menu = false; download(e) })
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

private fun copyToCache(ctx: android.content.Context, uri: Uri): File? = try {
    var name = "file"
    ctx.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { c ->
        if (c.moveToFirst()) name = c.getString(0) ?: name
    }
    val dir = File(ctx.cacheDir, "upload").apply { mkdirs() }
    val out = File(dir, sanitizeName(name))
    ctx.contentResolver.openInputStream(uri)!!.use { i -> out.outputStream().use { o -> i.copyTo(o) } }
    out
} catch (e: Exception) { null }

// ------------------------------------------------------------------------------------------------ transfers

@Composable
private fun TransfersScreen(engine: Engine) {
    val all by engine.transfers.collectAsStateWithLifecycle()
    val list = all.values.reversed()
    Column(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 12.dp)) {
            Title("Transfers", Modifier.weight(1f))
            TextButton({ engine.clearFinishedTransfers() }) { Text("Clear finished", color = accentText()) }
        }
        if (list.isEmpty()) {
            Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Box(Modifier.size(84.dp).clip(CircleShape).background(MaterialTheme.colorScheme.surfaceVariant), contentAlignment = Alignment.Center) {
                        Icon(Icons.Filled.SwapVert, null, Modifier.size(36.dp), tint = accentText())
                    }
                    Text("No transfers yet", Modifier.padding(top = 14.dp), fontWeight = FontWeight.Bold, color = MaterialTheme.colorScheme.onBackground)
                }
            }
        }
        LazyColumn(contentPadding = PaddingValues(top = 8.dp, bottom = 104.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            items(list, key = { it.tid }) { t ->
                Panel {
                    Text("${if (t.direction == "in") "⬇" else "⬆"} ${t.name}", maxLines = 1, color = MaterialTheme.colorScheme.onSurface,
                        fontWeight = FontWeight.SemiBold)
                    if (t.state == "active") {
                        LinearProgressIndicator(
                            progress = { if (t.size > 0) (t.done.toFloat() / t.size).coerceIn(0f, 1f) else 0f },
                            modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp).height(8.dp).clip(CircleShape),
                            color = MaterialTheme.colorScheme.primary, trackColor = MaterialTheme.colorScheme.surfaceVariant,
                        )
                        Dim("${humanSize(t.done)} / ${humanSize(t.size)}")
                    } else {
                        Dim(when (t.state) {
                            "done" -> "Done · ${humanSize(t.size)}" + (t.path?.let { "\n$it" } ?: "")
                            "cancelled" -> "Cancelled"
                            else -> "Failed: ${t.error ?: "unknown"}"
                        })
                    }
                }
            }
        }
    }
}

// ------------------------------------------------------------------------------------------------ settings

@Composable
private fun SettingsScreen(engine: Engine) {
    val ctx = LocalContext.current
    var name by remember { mutableStateOf(engine.state.deviceName) }
    var uploads by remember { mutableStateOf(engine.state.allowUploads) }
    var root by remember { mutableStateOf(engine.state.shareRoot) }

    LazyColumn(Modifier.fillMaxSize(), contentPadding = ScreenPadding, verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item { Title("Settings") }
        item {
            Panel {
                OutlinedTextField(name, { name = it.take(48) }, label = { Text("Device name") }, singleLine = true,
                    modifier = Modifier.fillMaxWidth(), shape = RoundedCornerShape(16.dp))
                TextButton({ engine.rename(name) }) { Text("Save name", color = accentText()) }
            }
        }
        item {
            Panel {
                OutlinedTextField(root, { root = it }, label = { Text("Shared folder (empty = all storage)") }, singleLine = true,
                    modifier = Modifier.fillMaxWidth(), shape = RoundedCornerShape(16.dp))
                Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 8.dp)) {
                    Text("Allow paired devices to upload", Modifier.weight(1f), color = MaterialTheme.colorScheme.onSurface)
                    Switch(uploads, { uploads = it })
                }
                LimeButton("Save sharing settings", Modifier.padding(top = 8.dp)) {
                    engine.setShareSettings(root.trim(), uploads)
                    Toast.makeText(ctx, "Saved — applies to new connections", Toast.LENGTH_SHORT).show()
                }
            }
        }
        item {
            Panel {
                Text("Access to all files: " + if (CloudLinkApp.hasAllFilesAccess()) "granted" else "not granted",
                    color = MaterialTheme.colorScheme.onSurface)
                if (!CloudLinkApp.hasAllFilesAccess()) OutlinedButton({ openAllFilesSettings(ctx) }, Modifier.padding(top = 8.dp)) { Text("Allow") }
            }
        }
        item {
            Panel {
                Text("Background server", fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurface)
                Row(Modifier.padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    LimeButton("Start") { ServerService.start(ctx) }
                    OutlinedButton({ ServerService.stop(ctx) }, shape = CircleShape) { Text("Stop") }
                }
                Dim("Everything stays on your local network. Only devices you paired by comparing a code can connect.",
                    Modifier.padding(top = 8.dp))
            }
        }
    }
}
