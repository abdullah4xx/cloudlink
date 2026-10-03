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
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.Devices
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.InsertDriveFile
import androidx.compose.material.icons.filled.SwapVert
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.UploadFile
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
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
        ServerService.start(this)   // keeps serving in the background
        setContent { MaterialTheme { CloudLinkUi(engine) } }
    }
}

private enum class Tab(val label: String) { Devices("Devices"), Files("Files"), Transfers("Transfers"), Settings("Settings") }

@OptIn(ExperimentalMaterial3Api::class)
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

    Scaffold(
        topBar = { TopAppBar(title = { Text("CloudLink") }) },
        bottomBar = {
            NavigationBar {
                for (t in Tab.entries) {
                    NavigationBarItem(
                        selected = tab == t, onClick = { tab = t },
                        icon = {
                            Icon(when (t) {
                                Tab.Devices -> Icons.Filled.Devices
                                Tab.Files -> Icons.Filled.Folder
                                Tab.Transfers -> Icons.Filled.SwapVert
                                Tab.Settings -> Icons.Filled.Settings
                            }, t.label)
                        },
                        label = { Text(if (t == Tab.Transfers && transfers.values.any { !it.finished }) "Transfers •" else t.label) },
                    )
                }
            }
        },
    ) { pad ->
        Box(Modifier.padding(pad).fillMaxSize()) {
            when (tab) {
                Tab.Devices -> DevicesScreen(engine, onOpen = { browsing = it; tab = Tab.Files })
                Tab.Files -> FilesScreen(engine, browsing, onPickDevice = { tab = Tab.Devices })
                Tab.Transfers -> TransfersScreen(engine)
                Tab.Settings -> SettingsScreen(engine)
            }
        }
    }
}

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
    val peers by engine.peers.collectAsStateWithLifecycle()
    var host by remember { mutableStateOf("") }
    var portText by remember { mutableStateOf("47616") }
    var confirmUnpair by remember { mutableStateOf<PeerInfo?>(null) }

    confirmUnpair?.let { peer ->
        AlertDialog(
            onDismissRequest = { confirmUnpair = null }, title = { Text("Forget ${peer.name}?") },
            text = { Text("It will lose access to this phone's files. Pair again to restore it.") },
            confirmButton = { Button({ engine.unpair(peer.id); confirmUnpair = null }) { Text("Forget") } },
            dismissButton = { TextButton({ confirmUnpair = null }) { Text("Cancel") } },
        )
    }

    LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        item {
            if (!CloudLinkApp.hasAllFilesAccess()) AllFilesBanner()
            Text("Devices on your Wi-Fi", style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(top = 12.dp))
            Text("This phone: ${engine.state.deviceName} · port ${engine.port}", fontSize = 12.sp)
        }
        if (peers.isEmpty()) item {
            Text("No devices found yet. Open CloudLink on the other device (same Wi-Fi), or connect by IP below.",
                modifier = Modifier.padding(vertical = 12.dp))
        }
        items(peers, key = { it.id }) { p ->
            Card(Modifier.fillMaxWidth().clickable(enabled = p.paired) { onOpen(p) }) {
                Row(Modifier.padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text(p.name.ifBlank { p.id }, style = MaterialTheme.typography.titleSmall)
                        Text("${if (p.online) "online" else "offline"} · ${p.host}:${p.port}", fontSize = 12.sp)
                    }
                    if (p.paired) {
                        TextButton({ confirmUnpair = p }) { Text("Forget") }
                    } else {
                        Button({ engine.startPairing(p.host, p.port) }) { Text("Pair") }
                    }
                }
            }
        }
        item {
            Text("Connect by IP", style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(top = 16.dp))
            Text("Use this if your router blocks discovery (guest Wi-Fi / client isolation).", fontSize = 12.sp)
            Row(Modifier.padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(host, { host = it.trim() }, label = { Text("IP address") }, singleLine = true,
                    modifier = Modifier.weight(1f), keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri))
                OutlinedTextField(portText, { portText = it.filter(Char::isDigit).take(5) }, label = { Text("Port") },
                    singleLine = true, modifier = Modifier.weight(0.5f),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number))
            }
            Button(
                onClick = {
                    val port = portText.toIntOrNull()
                    if (host.isEmpty() || port == null || port !in 1..65535) {
                        Toast.makeText(ctx, "Enter a valid IP and port", Toast.LENGTH_SHORT).show()
                    } else engine.startPairing(host, port)
                },
                modifier = Modifier.padding(top = 8.dp, bottom = 16.dp),
            ) { Text("Pair") }
        }
    }
}

@Composable
private fun AllFilesBanner() {
    val ctx = LocalContext.current
    Card(Modifier.fillMaxWidth().padding(top = 12.dp)) {
        Column(Modifier.padding(12.dp)) {
            Text("\"All files access\" is off", style = MaterialTheme.typography.titleSmall)
            Text("Paired devices can't browse your storage and downloads may fail until you allow it.", fontSize = 13.sp)
            OutlinedButton({ openAllFilesSettings(ctx) }, Modifier.padding(top = 8.dp)) { Text("Allow") }
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

@Composable
private fun FilesScreen(engine: Engine, peer: PeerInfo?, onPickDevice: () -> Unit) {
    if (peer == null) {
        Column(Modifier.fillMaxSize().padding(24.dp), verticalArrangement = Arrangement.Center, horizontalAlignment = Alignment.CenterHorizontally) {
            Text("Pick a paired device first.")
            Button(onPickDevice, Modifier.padding(top = 12.dp)) { Text("Devices") }
        }
        return
    }
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var path by remember(peer.id) { mutableStateOf("/") }
    var entries by remember(peer.id) { mutableStateOf<List<Entry>?>(null) }
    var error by remember(peer.id) { mutableStateOf<String?>(null) }
    var reload by remember(peer.id) { mutableStateOf(0) }

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

    Column(Modifier.fillMaxSize()) {
        Row(Modifier.padding(horizontal = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            IconButton(enabled = path != "/", onClick = { path = path.trimEnd('/').substringBeforeLast('/', "").ifEmpty { "/" } }) {
                Icon(Icons.Filled.ArrowBack, "Up")
            }
            Column(Modifier.weight(1f)) {
                Text(peer.name, style = MaterialTheme.typography.titleSmall)
                Text(path, fontSize = 12.sp, fontFamily = FontFamily.Monospace)
            }
            IconButton({ picker.launch("*/*") }) { Icon(Icons.Filled.UploadFile, "Upload here") }
        }
        Box(Modifier.fillMaxSize()) {
            val list = entries
            when {
                error != null -> Column(Modifier.padding(24.dp)) {
                    Text("Couldn't load: $error")
                    Button({ reload++ }, Modifier.padding(top = 8.dp)) { Text("Retry") }
                }
                list == null -> CircularProgressIndicator(Modifier.align(Alignment.Center))
                list.isEmpty() -> Text("Empty folder", Modifier.padding(24.dp))
                else -> LazyColumn {
                    items(list, key = { it.name }) { e ->
                        Row(
                            Modifier.fillMaxWidth().clickable {
                                if (e.isDir) path = (if (path == "/") "" else path) + "/" + e.name
                                else scope.launch {
                                    try {
                                        engine.download(peer.id, (if (path == "/") "" else path) + "/" + e.name)
                                        Toast.makeText(ctx, "Downloading ${e.name}…", Toast.LENGTH_SHORT).show()
                                    } catch (ex: Exception) { Toast.makeText(ctx, ex.message ?: "Failed", Toast.LENGTH_LONG).show() }
                                }
                            }.padding(horizontal = 16.dp, vertical = 10.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Icon(if (e.isDir) Icons.Filled.Folder else Icons.Filled.InsertDriveFile, null)
                            Column(Modifier.padding(start = 12.dp)) {
                                Text(e.name)
                                Text(
                                    (if (e.isDir) "" else humanSize(e.size) + " · ") +
                                        DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.SHORT).format(Date(e.mtime)),
                                    fontSize = 12.sp,
                                )
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
            Text("Transfers", style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
            TextButton({ engine.clearFinishedTransfers() }) { Text("Clear finished") }
        }
        if (list.isEmpty()) Text("Nothing yet.", Modifier.padding(top = 12.dp))
        LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            items(list, key = { it.tid }) { t ->
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(12.dp)) {
                        Text("${if (t.direction == "in") "⬇" else "⬆"} ${t.name}")
                        if (t.state == "active") {
                            LinearProgressIndicator(
                                progress = { if (t.size > 0) (t.done.toFloat() / t.size).coerceIn(0f, 1f) else 0f },
                                modifier = Modifier.fillMaxWidth().padding(vertical = 6.dp),
                            )
                            Text("${humanSize(t.done)} / ${humanSize(t.size)}", fontSize = 12.sp)
                        } else {
                            Text(
                                when (t.state) {
                                    "done" -> "Done · ${humanSize(t.size)}" + (t.path?.let { "\n$it" } ?: "")
                                    "cancelled" -> "Cancelled"
                                    else -> "Failed: ${t.error ?: "unknown"}"
                                }, fontSize = 12.sp,
                            )
                        }
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

    LazyColumn(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item {
            OutlinedTextField(name, { name = it.take(48) }, label = { Text("Device name") }, singleLine = true,
                modifier = Modifier.fillMaxWidth())
            TextButton({ engine.rename(name) }) { Text("Save name") }
        }
        item {
            OutlinedTextField(root, { root = it }, label = { Text("Shared folder (empty = all storage)") }, singleLine = true,
                modifier = Modifier.fillMaxWidth())
            Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 8.dp)) {
                Text("Allow paired devices to upload", Modifier.weight(1f))
                Switch(uploads, { uploads = it })
            }
            Button({
                engine.setShareSettings(root.trim(), uploads)
                Toast.makeText(ctx, "Saved — applies to new connections", Toast.LENGTH_SHORT).show()
            }, Modifier.padding(top = 8.dp)) { Text("Save sharing settings") }
        }
        item {
            Text("Access to all files: " + if (CloudLinkApp.hasAllFilesAccess()) "granted" else "not granted")
            if (!CloudLinkApp.hasAllFilesAccess()) OutlinedButton({ openAllFilesSettings(ctx) }) { Text("Allow") }
        }
        item {
            Text("Background server", style = MaterialTheme.typography.titleSmall)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button({ ServerService.start(ctx) }) { Text("Start") }
                OutlinedButton({ ServerService.stop(ctx) }) { Text("Stop") }
            }
            Text("Everything stays on your local network. Only devices you paired by comparing a code can connect.", fontSize = 12.sp,
                modifier = Modifier.padding(top = 8.dp))
        }
    }
}
