package app.cloudlink

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeout
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.security.GeneralSecurityException
import java.security.MessageDigest
import java.util.concurrent.ConcurrentHashMap

/**
 * Mirror of linux_app/core/peer.py: encrypted control messages + chunked file transfer over one authenticated
 * connection. Transport-agnostic: the engine passes in send functions.
 */
class PeerSession(
    private val peerId: String,
    private val encKey: ByteArray,
    private val sendText: (String) -> Unit,
    private val sendBinary: (ByteArray) -> Unit,
    private val scope: CoroutineScope,
    private val onTransfer: (TransferInfo) -> Unit,
    private val onToast: (String) -> Unit,
    private val downloadDir: File,
    private val shareRoot: File?,
    private val writable: Boolean,
    private val hideDotfiles: Boolean = true,
) {
    companion object {
        const val PAGE_SIZE = 300
        const val MAX_FILE = 1L shl 42
        const val EMIT_INTERVAL_MS = 100L
        private val TID_RE = Regex("[0-9a-f]{32}")
    }

    private class In(
        val tid: ByteArray, val key: ByteArray, val name: String, val size: Long,
        val path: File, val tmp: File, val fh: FileOutputStream,
    ) {
        val digest: MessageDigest = MessageDigest.getInstance("SHA-256")
        var seq = 0
        var received = 0L
        var lastEmit = 0L
    }

    private class Out {
        @Volatile var cancelled = false
        @Volatile var error = ""
        val ack = CompletableDeferred<JSONObject>()
    }

    private val pending = ConcurrentHashMap<String, CompletableDeferred<JSONObject>>()
    private val incoming = ConcurrentHashMap<String, In>()
    private val outgoing = ConcurrentHashMap<String, Out>()
    private val rejected = ConcurrentHashMap<String, String>()

    private fun sendCtl(o: JSONObject) = sendText(Crypto.sealCtl(encKey, o.toString()))

    private fun emitT(tid: String, name: String, size: Long, done: Long, dir: String, state: String,
                      error: String? = null, path: String? = null) =
        onTransfer(TransferInfo(peerId, tid, name, size, done, dir, state, error, path))

    // ---------------------------------------------------------------- receive
    fun handleCtl(blob: String) {
        val msg = try {
            JSONObject(Crypto.openCtl(encKey, blob))
        } catch (e: Exception) {
            onToast("Ignored an undecryptable message"); return
        }
        try {
            when (msg.optString("type")) {
                "list_result" -> pending.remove(msg.optString("id"))?.complete(msg)
                "get_error" -> onToast("Download failed: " + msg.optString("error", "error"))
                "offer" -> onOffer(msg)
                "done" -> onDone(msg)
                "ack" -> outgoing[msg.optString("tid")]?.ack?.complete(msg)
                "cancel" -> onCancel(msg)
                "list" -> if (shareRoot != null) serveList(msg)
                "get" -> if (shareRoot != null) scope.launch { serveGet(msg) }
            }
        } catch (e: Exception) {
            onToast("Protocol error: ${e.message}")
        }
    }

    fun handleBinary(frame: ByteArray) {
        val f = try { Crypto.parseFrame(frame) } catch (e: IllegalArgumentException) { return }
        val tidh = f.tid.toHex()
        val inc = incoming[tidh] ?: return
        if (f.seq != inc.seq) return failIn(inc, "out_of_order")
        val plain = try {
            Crypto.openChunk(inc.key, f.tid, f.seq, f.ct).also { inc.fh.write(it) }
        } catch (e: GeneralSecurityException) {
            return failIn(inc, "tampered")
        } catch (e: IOException) {
            return failIn(inc, "disk: ${e.message}")
        }
        inc.digest.update(plain)
        inc.seq++
        inc.received += plain.size
        if (inc.received > MAX_FILE) return failIn(inc, "too_large")
        val now = System.currentTimeMillis()
        if (now - inc.lastEmit >= EMIT_INTERVAL_MS) {
            inc.lastEmit = now
            emitT(tidh, inc.name, inc.size, inc.received, "in", "active")
        }
    }

    private fun rejectOffer(tidh: String, name: String, size: Long, why: String) {
        if (rejected.size > 64) rejected.clear()
        rejected[tidh] = why
        sendCtl(JSONObject().put("type", "cancel").put("tid", tidh).put("error", why))
        emitT(tidh, name, size, 0, "in", "failed", error = why)
    }

    private fun onOffer(m: JSONObject) {
        val tidh = m.optString("tid")
        if (!TID_RE.matches(tidh) || incoming.containsKey(tidh)) return
        val sizeAny = m.opt("size")
        val size = (sizeAny as? Number)?.toLong() ?: return
        if (size < 0 || size > MAX_FILE) return
        val name = sanitizeName(m.optString("name", "file"))
        val dest = if (m.has("dest") && !m.isNull("dest")) m.optString("dest") else null
        val dir: File
        if (dest == null) {
            dir = downloadDir
            if (!dir.isDirectory && !dir.mkdirs()) return rejectOffer(tidh, name, size, "disk")
        } else {
            if (shareRoot == null || !writable) return rejectOffer(tidh, name, size, "forbidden")
            val d = resolveUnder(shareRoot, dest)
            if (d == null || !d.isDirectory || isHidden(d)) return rejectOffer(tidh, name, size, "bad_dest")
            dir = d
        }
        val path = uniquePath(dir, name)
        val tmp = File(path.path + ".part")
        val fh = try { FileOutputStream(tmp) } catch (e: IOException) { return rejectOffer(tidh, name, size, e.message ?: "disk") }
        val tid = tidh.hexToBytes()
        incoming[tidh] = In(tid, Crypto.xferKey(encKey, tid), name, size, path, tmp, fh)
        emitT(tidh, name, size, 0, "in", "active")
    }

    private fun isHidden(p: File): Boolean {
        val root = shareRoot ?: return false
        if (!hideDotfiles) return false
        val rel = try {
            p.canonicalFile.relativeToOrNull(root.canonicalFile) ?: return true
        } catch (e: IOException) { return true }
        return rel.path.split(File.separatorChar).any { it.startsWith(".") }
    }

    private fun onDone(m: JSONObject) {
        val tidh = m.optString("tid")
        val inc = incoming.remove(tidh)
        if (inc == null) {
            sendCtl(JSONObject().put("type", "ack").put("tid", tidh).put("ok", false)
                .put("error", rejected.remove(tidh) ?: "unknown_transfer"))
            return
        }
        try { inc.fh.close() } catch (_: IOException) {}
        val good = m.optInt("chunks", -1) == inc.seq && m.optLong("size", -1) == inc.received &&
            m.optString("sha256") == inc.digest.digest().toHex()
        if (good && inc.tmp.renameTo(inc.path)) {
            emitT(tidh, inc.name, inc.received, inc.received, "in", "done", path = inc.path.path)
            sendCtl(JSONObject().put("type", "ack").put("tid", tidh).put("ok", true))
        } else {
            inc.tmp.delete()
            emitT(tidh, inc.name, inc.size, inc.received, "in", "failed", error = "integrity")
            sendCtl(JSONObject().put("type", "ack").put("tid", tidh).put("ok", false).put("error", "integrity"))
        }
    }

    private fun onCancel(m: JSONObject) {
        val tidh = m.optString("tid")
        incoming.remove(tidh)?.let { inc ->
            try { inc.fh.close() } catch (_: IOException) {}
            inc.tmp.delete()
            emitT(tidh, inc.name, inc.size, inc.received, "in", "cancelled")
        }
        outgoing[tidh]?.let { out ->
            out.cancelled = true
            out.error = m.optString("error", "")
            out.ack.complete(JSONObject().put("ok", false).put("error", out.error.ifEmpty { "cancelled" }))
        }
    }

    private fun failIn(inc: In, why: String) {
        val tidh = inc.tid.toHex()
        incoming.remove(tidh)
        try { inc.fh.close() } catch (_: IOException) {}
        inc.tmp.delete()
        emitT(tidh, inc.name, inc.size, inc.received, "in", "failed", error = why)
        try { sendCtl(JSONObject().put("type", "cancel").put("tid", tidh)) } catch (_: Exception) {}
    }

    // ---------------------------------------------------------------- requests to the peer
    suspend fun requestList(path: String, offset: Int = 0, timeoutMs: Long = 20_000): JSONObject {
        val rid = Crypto.randomBytes(8).toHex()
        val fut = CompletableDeferred<JSONObject>()
        pending[rid] = fut
        try {
            sendCtl(JSONObject().put("type", "list").put("id", rid).put("path", path).put("offset", offset))
            return withTimeout(timeoutMs) { fut.await() }
        } finally {
            pending.remove(rid)
        }
    }

    suspend fun requestListAll(path: String): List<Entry> {
        val all = ArrayList<Entry>()
        var offset = 0
        while (true) {
            val res = requestList(path, offset)
            if (!res.optBoolean("ok")) throw IOException(res.optString("error", "list_failed"))
            val arr = res.optJSONArray("entries") ?: JSONArray()
            for (i in 0 until arr.length()) {
                val e = arr.getJSONObject(i)
                all += Entry(e.optString("n"), e.optBoolean("d"), e.optLong("s"), e.optLong("m"))
            }
            if (!res.optBoolean("more") || arr.length() == 0) return all
            offset += arr.length()
        }
    }

    fun requestGet(path: String) =
        sendCtl(JSONObject().put("type", "get").put("id", Crypto.randomBytes(8).toHex()).put("path", path))

    // ---------------------------------------------------------------- send a file
    suspend fun sendFile(file: File, replyTo: String? = null, dest: String? = null): Boolean {
        val size = file.length()
        val name = sanitizeName(file.name)
        val tid = Crypto.randomBytes(16)
        val tidh = tid.toHex()
        val key = Crypto.xferKey(encKey, tid)
        val out = Out()
        outgoing[tidh] = out
        var sent = 0L
        var lastEmit = 0L
        emitT(tidh, name, size, 0, "out", "active")
        try {
            val offer = JSONObject().put("type", "offer").put("tid", tidh).put("name", name).put("size", size)
            if (replyTo != null) offer.put("reply_to", replyTo)
            if (dest != null) offer.put("dest", dest)
            sendCtl(offer)
            val digest = MessageDigest.getInstance("SHA-256")
            var seq = 0
            file.inputStream().use { fin ->
                val buf = ByteArray(Crypto.CHUNK)
                while (true) {
                    if (out.cancelled) {
                        emitT(tidh, name, size, sent, "out", if (out.error.isNotEmpty()) "failed" else "cancelled",
                            error = out.error.ifEmpty { null })
                        return false
                    }
                    val n = fin.read(buf)
                    if (n <= 0) break
                    val chunk = if (n == buf.size) buf else buf.copyOf(n)
                    sendBinary(Crypto.sealChunk(key, tid, seq, chunk))
                    digest.update(chunk)
                    seq++
                    sent += n
                    val now = System.currentTimeMillis()
                    if (now - lastEmit >= EMIT_INTERVAL_MS) {
                        lastEmit = now
                        emitT(tidh, name, size, sent, "out", "active")
                    }
                }
            }
            sendCtl(JSONObject().put("type", "done").put("tid", tidh).put("chunks", seq).put("size", sent)
                .put("sha256", digest.digest().toHex()))
            val ack = withTimeout(120_000) { out.ack.await() }
            val ok = ack.optBoolean("ok")
            emitT(tidh, name, sent, sent, "out", if (ok) "done" else "failed",
                error = if (ok) null else ack.optString("error", "rejected"))
            return ok
        } catch (e: TimeoutCancellationException) {
            emitT(tidh, name, size, sent, "out", "failed", error = "timeout")
            return false
        } catch (e: kotlinx.coroutines.CancellationException) {
            throw e
        } catch (e: Exception) {
            emitT(tidh, name, size, sent, "out", "failed", error = e.message ?: e.javaClass.simpleName)
            return false
        } finally {
            outgoing.remove(tidh)
        }
    }

    // ---------------------------------------------------------------- serve list/get
    private fun serveList(m: JSONObject) {
        val rid = m.optString("id")
        val root = shareRoot ?: return
        val reqPath = m.optString("path", "/")
        fun fail(why: String) = sendCtl(JSONObject().put("type", "list_result").put("id", rid).put("ok", false).put("error", why))
        val d = resolveUnder(root, reqPath)
        if (d == null || !d.isDirectory || isHidden(d)) return fail("not_found")
        val offset = maxOf(0, m.optInt("offset", 0))
        val items = (d.listFiles() ?: return fail("denied"))
            .filter { !(hideDotfiles && it.name.startsWith(".")) }
            .sortedWith(compareBy({ !it.isDirectory }, { it.name.lowercase() }))
        val page = items.drop(offset).take(PAGE_SIZE)
        val arr = JSONArray()
        for (p in page) {
            arr.put(JSONObject().put("n", p.name).put("d", p.isDirectory)
                .put("s", if (p.isDirectory) 0L else p.length()).put("m", p.lastModified()))
        }
        sendCtl(JSONObject().put("type", "list_result").put("id", rid).put("ok", true).put("path", reqPath)
            .put("entries", arr).put("more", offset + PAGE_SIZE < items.size))
    }

    private suspend fun serveGet(m: JSONObject) {
        val root = shareRoot ?: return
        val p = resolveUnder(root, m.optString("path", ""))
        if (p == null || !p.isFile || isHidden(p)) {
            sendCtl(JSONObject().put("type", "get_error").put("id", m.optString("id")).put("error", "not_found"))
            return
        }
        sendFile(p, replyTo = m.optString("id"))
    }

    // ---------------------------------------------------------------- on disconnect
    fun abortAll() {
        for ((tidh, inc) in incoming) {
            try { inc.fh.close() } catch (_: IOException) {}
            inc.tmp.delete()
            emitT(tidh, inc.name, inc.size, inc.received, "in", "failed", error = "disconnected")
        }
        incoming.clear()
        for (o in outgoing.values) {
            o.cancelled = true
            o.ack.completeExceptionally(IOException("disconnected"))
        }
        for (f in pending.values) f.completeExceptionally(IOException("disconnected"))
        pending.clear()
    }
}
