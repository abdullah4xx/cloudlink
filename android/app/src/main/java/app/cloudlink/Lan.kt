package app.cloudlink

import org.json.JSONException
import org.json.JSONObject
import java.io.BufferedInputStream
import java.io.BufferedOutputStream
import java.io.DataInputStream
import java.net.InetSocketAddress
import java.net.Socket
import java.nio.ByteBuffer
import java.security.MessageDigest

/** Mirror of linux_app/core/lan.py: u32 BE length(type+payload) | u8 type | payload. */
object Lan {
    const val T_HS = 0
    const val T_CTL = 1
    const val T_BIN = 2
    const val MAX_HS = 4096
    const val MAX_FRAME = 1 shl 20

    fun open(host: String, port: Int, timeoutMs: Int = 6000): Conn {
        val s = Socket()
        s.tcpNoDelay = true
        s.connect(InetSocketAddress(host, port), timeoutMs)
        return Conn(s)
    }

    private fun hexField(m: JSONObject, key: String, n: Int? = null): ByteArray {
        val b = m.optString(key, "").hexToBytes()
        if (n != null && b.size != n) throw LanException("bad_handshake")
        return b
    }

    /** Initiator side. Authenticates both sides; returns the per-connection session key. */
    fun clientHandshake(conn: Conn, myId: String, peerId: String, authKey: ByteArray, encKey: ByteArray): ByteArray {
        val nc = Crypto.randomBytes(16)
        conn.sendJson(JSONObject().put("t", "hello").put("v", 2).put("id", myId).put("nc", nc.toHex()))
        val m = conn.recvJson()
        if (m.optString("t") == "error") throw LanException(m.optString("code", "error"))
        if (m.optString("t") != "hello_ok") throw LanException("bad_handshake")
        val ns = hexField(m, "ns", 16)
        val expect = Crypto.mac(authKey, "srv".toByteArray(), myId.toByteArray(), peerId.toByteArray(), nc, ns)
        if (!MessageDigest.isEqual(expect, hexField(m, "mac", 32))) throw LanException("server_auth_failed")
        val mine = Crypto.mac(authKey, "cli".toByteArray(), myId.toByteArray(), peerId.toByteArray(), nc, ns)
        conn.sendJson(JSONObject().put("t", "auth").put("mac", mine.toHex()))
        return Crypto.sessionKey(encKey, nc, ns)
    }

    class Accepted(val peerId: String, val sessionKey: ByteArray)

    /** Responder side; [hello] was already read. [lookup] returns the stored peer record (auth_key/enc_key hex). */
    fun serverHandshake(conn: Conn, myId: String, hello: JSONObject, lookup: (String) -> StateStore.Peer?): Accepted {
        if (hello.optInt("v", -1) != 2) throw LanException("bad_version")
        val cid = hello.optString("id", "")
        val nc = hexField(hello, "nc", 16)
        val peer = lookup(cid)
        if (peer == null) {
            conn.sendJson(JSONObject().put("t", "error").put("code", "not_paired"))
            throw LanException("not_paired")
        }
        val authKey = peer.authKey.hexToBytes()
        val encKey = peer.encKey.hexToBytes()
        val ns = Crypto.randomBytes(16)
        val srvMac = Crypto.mac(authKey, "srv".toByteArray(), cid.toByteArray(), myId.toByteArray(), nc, ns)
        conn.sendJson(JSONObject().put("t", "hello_ok").put("ns", ns.toHex()).put("mac", srvMac.toHex()))
        val a = conn.recvJson()
        val expect = Crypto.mac(authKey, "cli".toByteArray(), cid.toByteArray(), myId.toByteArray(), nc, ns)
        val got = try { a.optString("mac", "").hexToBytes() } catch (e: LanException) { ByteArray(0) }
        if (a.optString("t") != "auth" || !MessageDigest.isEqual(expect, got)) {
            conn.sendJson(JSONObject().put("t", "error").put("code", "auth_failed"))
            throw LanException("auth_failed")
        }
        return Accepted(cid, Crypto.sessionKey(encKey, nc, ns))
    }
}

class Conn(val socket: Socket) {
    private val inp = DataInputStream(BufferedInputStream(socket.getInputStream(), 64 * 1024))
    private val out = BufferedOutputStream(socket.getOutputStream(), 64 * 1024)
    private val sendLock = Any()
    @Volatile var closed = false
        private set

    val remoteHost: String
        get() = (socket.remoteSocketAddress as? InetSocketAddress)?.address?.hostAddress ?: ""

    fun send(type: Int, payload: ByteArray) {
        if (closed) throw LanException("closed")
        val hdr = ByteBuffer.allocate(5).putInt(payload.size + 1).put(type.toByte()).array()
        synchronized(sendLock) {
            out.write(hdr)
            out.write(payload)
            out.flush()
        }
    }

    fun recv(limit: Int = Lan.MAX_FRAME): Pair<Int, ByteArray> {
        val n = inp.readInt()
        val type = inp.readUnsignedByte()
        if (n < 1 || n - 1 > limit) throw LanException("bad_frame")
        val p = ByteArray(n - 1)
        inp.readFully(p)
        return type to p
    }

    fun sendJson(o: JSONObject) = send(Lan.T_HS, o.toString().toByteArray(Charsets.UTF_8))

    /** Handshake JSON with a read timeout (the socket goes back to blocking afterwards). */
    fun recvJson(timeoutMs: Int = 10_000): JSONObject {
        socket.soTimeout = timeoutMs
        try {
            val (t, p) = recv(Lan.MAX_HS)
            if (t != Lan.T_HS) throw LanException("expected_handshake")
            return try { JSONObject(String(p, Charsets.UTF_8)) } catch (e: JSONException) { throw LanException("bad_json") }
        } finally {
            if (!closed) socket.soTimeout = 0
        }
    }

    fun close() {
        if (closed) return
        closed = true
        try { socket.close() } catch (_: Exception) {}
    }
}
