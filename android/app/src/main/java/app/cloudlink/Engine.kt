package app.cloudlink

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withTimeoutOrNull
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.security.MessageDigest
import java.util.Base64
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicBoolean

class EngineError(msg: String) : Exception(msg)

/**
 * LAN engine: server + client in one process, no internet, no relay. Port of linux_app/core/engine.py.
 * UI observes [peers], [transfers], [pairing], [toasts]; the foreground service keeps it alive.
 */
class Engine(
    val state: StateStore,
    private val downloadDir: File,
    private val shareRootProvider: () -> File,
    private val beaconPort: Int = Discovery.PORT,
    private val beaconTargets: List<InetSocketAddress>? = null,
    private val listenHost: String? = null,   // null = all interfaces
    private val context: android.content.Context? = null,   // enables mDNS discovery when given
) {
    private class Link(val peerId: String, val conn: Conn, val session: PeerSession) {
        var job: Job? = null
    }

    @Volatile private var scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var server: ServerSocket? = null
    private var discovery: Discovery? = null
    @Volatile var port = 0
        private set
    @Volatile var running = false
        private set

    private val _peers = MutableStateFlow<List<PeerInfo>>(emptyList())
    val peers: StateFlow<List<PeerInfo>> = _peers
    private val _transfers = MutableStateFlow<Map<String, TransferInfo>>(emptyMap())
    val transfers: StateFlow<Map<String, TransferInfo>> = _transfers
    private val _pairing = MutableStateFlow<Pairing>(Pairing.Idle)
    val pairing: StateFlow<Pairing> = _pairing
    private val _toasts = MutableSharedFlow<String>(extraBufferCapacity = 16)
    val toasts: SharedFlow<String> = _toasts
    /** Emits the peer id when a paired device rejects us (e.g. it unpaired us). */
    private val _rejected = MutableSharedFlow<String>(extraBufferCapacity = 4)
    val rejected: SharedFlow<String> = _rejected

    private val links = ConcurrentHashMap<String, Link>()
    private val linkLocks = ConcurrentHashMap<String, Mutex>()
    private val handlers = ConcurrentHashMap.newKeySet<Job>()
    private var pairJob: Job? = null
    private val pairBusy = AtomicBoolean(false)
    @Volatile private var answer: CompletableDeferred<Boolean>? = null

    // ------------------------------------------------------------------ lifecycle
    @Synchronized
    fun start() {
        if (running) return
        scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)  // a stopped engine can be started again
        var srv: ServerSocket? = null
        val base = state.port
        for (p in (if (base == 0) listOf(0) else (base until base + 20).toList())) {
            try {
                srv = ServerSocket().apply {
                    reuseAddress = true
                    bind(if (listenHost == null) InetSocketAddress(p) else InetSocketAddress(listenHost, p))
                }
                break
            } catch (_: IOException) {
            }
        }
        val s = srv ?: throw EngineError("no free TCP port")
        server = s
        port = s.localPort
        val d = Discovery(state.deviceId, { state.deviceName }, { port }, ::publishPeers, beaconPort, beaconTargets, context = context)
        try { d.start(scope) } catch (e: Exception) { toast("Discovery unavailable: ${e.message} — use 'connect by IP'") }
        discovery = d
        running = true
        scope.launch { acceptLoop(s) }
        publishPeers()
    }

    @Synchronized
    fun stop() {
        if (!running) return
        running = false
        discovery?.stop()
        try { server?.close() } catch (_: Exception) {}
        pairJob?.cancel()
        answer?.complete(false)
        links.values.forEach { it.conn.close() }
        links.clear()
        handlers.forEach { it.cancel() }
        scope.coroutineContext[Job]?.cancel()
    }

    private fun toast(t: String) { _toasts.tryEmit(t) }

    private fun onTransfer(t: TransferInfo) {
        _transfers.update { cur ->
            val m = LinkedHashMap(cur)
            m[t.tid] = t
            if (m.size > 100) m.keys.filter { m[it]?.finished == true }.take(m.size - 100).forEach { m.remove(it) }
            m
        }
    }

    fun clearFinishedTransfers() = _transfers.update { it.filterValues { t -> !t.finished } }

    // ------------------------------------------------------------------ peers view
    private fun publishPeers() {
        val seen = discovery?.peers ?: emptyMap<String, Discovery.Seen>()
        val out = ArrayList<PeerInfo>()
        val paired = state.allPeers()
        for ((pid, p) in paired) {
            val s = seen[pid]
            out += PeerInfo(pid, s?.name ?: p.name, true, s != null, s?.host ?: p.host, s?.port ?: p.port)
        }
        for ((pid, s) in seen) if (pid !in paired) out += PeerInfo(pid, s.name, false, true, s.host, s.port)
        _peers.value = out
    }

    private fun address(peerId: String): Pair<String, Int> {
        discovery?.peers?.get(peerId)?.let { return it.host to it.port }
        val p = state.peer(peerId)
        if (p != null && p.host.isNotEmpty() && p.port > 0) return p.host to p.port
        throw EngineError("Device not found on the network")
    }

    // ------------------------------------------------------------------ sessions
    private fun makeSession(peerId: String, conn: Conn, key: ByteArray) = PeerSession(
        peerId, key,
        sendText = { conn.send(Lan.T_CTL, it.toByteArray(Charsets.US_ASCII)) },
        sendBinary = { conn.send(Lan.T_BIN, it) },
        scope = scope, onTransfer = ::onTransfer, onToast = ::toast,
        downloadDir = downloadDir, shareRoot = shareRootProvider(), writable = state.allowUploads,
    )

    private fun reader(link: Link) {
        val conn = link.conn
        try {
            while (true) {
                val (typ, payload) = conn.recv()
                when (typ) {
                    Lan.T_CTL -> link.session.handleCtl(String(payload, Charsets.US_ASCII))
                    Lan.T_BIN -> link.session.handleBinary(payload)
                    else -> throw LanException("bad_frame_type")
                }
            }
        } catch (_: Exception) {
            // connection ended or sent garbage
        } finally {
            link.session.abortAll()
            conn.close()
            links.remove(link.peerId, link)
        }
    }

    private suspend fun link(peerId: String): Link = linkLocks.getOrPut(peerId) { Mutex() }.withLock {
        links[peerId]?.takeIf { !it.conn.closed }?.let { return@withLock it }
        val peer = state.peer(peerId) ?: throw EngineError("not_paired")
        val (host, port) = address(peerId)
        val conn = try { kotlinx.coroutines.withContext(Dispatchers.IO) { Lan.open(host, port) } }
        catch (e: IOException) { throw EngineError("Cannot reach device (${e.javaClass.simpleName})") }
        val key = try {
            kotlinx.coroutines.withContext(Dispatchers.IO) {
                Lan.clientHandshake(conn, state.deviceId, peerId, peer.authKey.hexToBytes(), peer.encKey.hexToBytes())
            }
        } catch (e: LanException) {
            conn.close()
            if (e.message in setOf("not_paired", "auth_failed", "server_auth_failed")) _rejected.tryEmit(peerId)
            throw EngineError("Handshake failed: ${e.message}")
        } catch (e: IOException) {
            conn.close()
            throw EngineError("Cannot reach device (${e.javaClass.simpleName})")
        }
        val l = Link(peerId, conn, makeSession(peerId, conn, key))
        l.job = scope.launch { reader(l) }
        links[peerId] = l
        l
    }

    // ------------------------------------------------------------------ incoming connections
    private fun acceptLoop(s: ServerSocket) {
        while (!s.isClosed) {
            val sock = try { s.accept() } catch (e: IOException) { return }
            sock.tcpNoDelay = true
            val job = scope.launch { handleClient(Conn(sock)) }
            handlers += job
            job.invokeOnCompletion { handlers -= job }
        }
    }

    private suspend fun handleClient(conn: Conn) {
        try {
            val first = conn.recvJson()
            when (first.optString("t")) {
                "hello" -> {
                    val acc = Lan.serverHandshake(conn, state.deviceId, first) { state.peer(it) }
                    reader(Link(acc.peerId, conn, makeSession(acc.peerId, conn, acc.sessionKey)))
                }
                "pair_req" -> pairRespond(conn, first)
            }
        } catch (e: CancellationException) {
            throw e
        } catch (_: Exception) {
        } finally {
            conn.close()
        }
    }

    // ------------------------------------------------------------------ UI API
    suspend fun listDir(peerId: String, path: String): List<Entry> = link(peerId).session.requestListAll(path)

    suspend fun download(peerId: String, path: String) = link(peerId).session.requestGet(path)

    suspend fun upload(peerId: String, file: File, destDir: String = "/"): Boolean =
        link(peerId).session.sendFile(file, dest = destDir)

    fun unpair(peerId: String) {
        links.remove(peerId)?.conn?.close()
        state.removePeer(peerId)
        publishPeers()
    }

    fun setShareSettings(shareRoot: String, allowUploads: Boolean) {
        state.update { this.shareRoot = shareRoot; this.allowUploads = allowUploads }
    }

    fun rename(name: String) { state.update { deviceName = name.trim().take(48).ifEmpty { deviceName } } }

    // ------------------------------------------------------------------ pairing
    fun answerPairing(ok: Boolean) { answer?.complete(ok) }

    /** Registers the answer slot BEFORE showing [question], so a fast UI answer can never be lost. */
    private suspend fun askUser(timeoutMs: Long, question: Pairing): Boolean {
        val d = CompletableDeferred<Boolean>()
        answer = d
        _pairing.value = question
        return try { withTimeoutOrNull(timeoutMs) { d.await() } ?: false } finally { answer = null }
    }

    fun startPairing(host: String, port: Int) {
        pairJob?.cancel()
        pairJob = scope.launch { pairInitiate(host, port) }
    }

    fun cancelPairing() {
        pairJob?.cancel()
        answer?.complete(false)
        if (_pairing.value !is Pairing.Done) _pairing.value = Pairing.Idle
    }

    fun dismissPairing() { _pairing.value = Pairing.Idle }

    private suspend fun pairInitiate(host: String, port: Int) {
        var conn: Conn? = null
        try {
            _pairing.value = Pairing.Connecting(host)
            conn = kotlinx.coroutines.withContext(Dispatchers.IO) { Lan.open(host, port) }
            val (priv, pub) = Crypto.generateKeypair()
            val nonce = Crypto.randomBytes(16)
            conn.sendJson(JSONObject().put("t", "pair_req").put("v", 2).put("id", state.deviceId)
                .put("name", state.deviceName).put("port", this.port).put("commit", Crypto.pairCommit(pub, nonce)))
            val m = conn.recvJson(75_000)   // the other user must accept
            if (m.optString("t") == "pair_failed") {
                _pairing.value = Pairing.Failed(m.optString("reason", "failed")); return
            }
            if (m.optString("t") != "pair_resp") throw LanException("bad_pairing")
            val peerPub = Base64.getDecoder().decode(m.getString("pub"))
            conn.sendJson(JSONObject().put("t", "pair_reveal").put("pub", Base64.getEncoder().encodeToString(pub))
                .put("nonce", nonce.toHex()))
            val keys = Crypto.deriveSession(Crypto.sharedSecret(priv, peerPub))
            pairFinish(conn, keys, m.getString("id"), m.optString("name").take(64), host, port)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            _pairing.value = Pairing.Failed(e.message ?: e.javaClass.simpleName)
        } finally {
            conn?.close()
        }
    }

    private suspend fun pairRespond(conn: Conn, req: JSONObject) {
        if (!pairBusy.compareAndSet(false, true)) {
            conn.sendJson(JSONObject().put("t", "pair_failed").put("reason", "busy")); return
        }
        try {
            val pid = req.getString("id")
            val name = req.optString("name").take(64)
            val commit = req.getString("commit")
            if (pid.isEmpty() || commit.length != 64) throw LanException("bad_pairing")
            if (!askUser(60_000, Pairing.Request(name, conn.remoteHost))) {
                conn.sendJson(JSONObject().put("t", "pair_failed").put("reason", "declined"))
                _pairing.value = Pairing.Failed("declined"); return
            }
            val (priv, pub) = Crypto.generateKeypair()
            conn.sendJson(JSONObject().put("t", "pair_resp").put("id", state.deviceId).put("name", state.deviceName)
                .put("pub", Base64.getEncoder().encodeToString(pub)))
            val r = conn.recvJson(30_000)
            if (r.optString("t") != "pair_reveal") throw LanException("bad_pairing")
            val peerPub = Base64.getDecoder().decode(r.getString("pub"))
            val ok = MessageDigest.isEqual(
                Crypto.pairCommit(peerPub, r.getString("nonce").hexToBytes()).toByteArray(), commit.toByteArray())
            if (!ok) {
                conn.sendJson(JSONObject().put("t", "pair_failed").put("reason", "commit_mismatch"))
                _pairing.value = Pairing.Failed("commit_mismatch"); return
            }
            val keys = Crypto.deriveSession(Crypto.sharedSecret(priv, peerPub))
            val port = req.optInt("port", 0)
            pairFinish(conn, keys, pid, name, conn.remoteHost, port)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            _pairing.value = Pairing.Failed(e.message ?: e.javaClass.simpleName)
        } finally {
            pairBusy.set(false)
        }
    }

    /** Both users compare the SAS; each side proves it derived the same keys. */
    private suspend fun pairFinish(conn: Conn, keys: Crypto.SessionKeys, pid: String, name: String, host: String, port: Int) {
        if (!askUser(120_000, Pairing.Verify(keys.sas, name))) {
            try { conn.sendJson(JSONObject().put("t", "pair_failed").put("reason", "declined")) } catch (_: Exception) {}
            _pairing.value = Pairing.Failed("declined"); return
        }
        val mine = Crypto.authHash(keys.authKey)
        conn.sendJson(JSONObject().put("t", "pair_confirm").put("h", mine))
        val m = conn.recvJson(120_000)
        if (m.optString("t") == "pair_failed") {
            _pairing.value = Pairing.Failed(m.optString("reason", "declined")); return
        }
        if (m.optString("t") != "pair_confirm" ||
            !MessageDigest.isEqual(m.optString("h").toByteArray(), mine.toByteArray())) {
            _pairing.value = Pairing.Failed("sas_mismatch"); return
        }
        state.addPeer(pid, name, keys.authKey, keys.encKey, host, port)
        _pairing.value = Pairing.Done(name)
        publishPeers()
    }
}
