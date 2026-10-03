package app.cloudlink

import org.bouncycastle.math.ec.rfc7748.X25519
import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer
import java.security.MessageDigest
import java.security.SecureRandom
import java.util.Base64
import javax.crypto.Cipher
import javax.crypto.Mac
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

/**
 * Mirror of linux_app/core/crypto.py — spec in protocol/PROTOCOL.md.
 * Verified against protocol/test_vectors.json (see CryptoTest).
 */
object Crypto {
    const val CHUNK = 32 * 1024
    const val FRAME_VERSION = 0x01
    const val FRAME_HEADER = 1 + 16 + 4
    private val CTL_AAD = "cloudlink/ctl/v1".toByteArray(Charsets.US_ASCII)
    private val rnd = SecureRandom()

    fun randomBytes(n: Int): ByteArray = ByteArray(n).also { rnd.nextBytes(it) }

    // ---- X25519 ----
    fun generateKeypair(): Pair<ByteArray, ByteArray> {
        val priv = randomBytes(32)
        return priv to publicFromPrivate(priv)
    }

    fun publicFromPrivate(priv: ByteArray): ByteArray {
        val out = ByteArray(32)
        X25519.scalarMultBase(priv, 0, out, 0)
        return out
    }

    fun sharedSecret(priv: ByteArray, peerPub: ByteArray): ByteArray {
        require(peerPub.size == 32) { "bad public key" }
        val out = ByteArray(32)
        // false = all-zero output (low-order point) → reject, like python's `cryptography`
        require(X25519.calculateAgreement(priv, 0, peerPub, 0, out, 0)) { "bad public key" }
        return out
    }

    // ---- HMAC / HKDF-SHA256 ----
    fun hmacSha256(key: ByteArray, vararg parts: ByteArray): ByteArray {
        val m = Mac.getInstance("HmacSHA256")
        m.init(SecretKeySpec(key, "HmacSHA256"))
        for (p in parts) m.update(p)
        return m.doFinal()
    }

    /** HKDF-SHA256 with an empty salt (= 32 zero bytes). */
    fun hkdf(ikm: ByteArray, info: ByteArray, length: Int = 32): ByteArray {
        val prk = hmacSha256(ByteArray(32), ikm)
        val out = ByteArrayOutputStream()
        var t = ByteArray(0)
        var i = 1
        while (out.size() < length) {
            t = hmacSha256(prk, t, info, byteArrayOf(i.toByte()))
            out.write(t)
            i++
        }
        return out.toByteArray().copyOf(length)
    }

    private fun ascii(s: String) = s.toByteArray(Charsets.US_ASCII)

    class SessionKeys(val authKey: ByteArray, val encKey: ByteArray, val sas: String)

    fun deriveSession(shared: ByteArray): SessionKeys {
        val auth = hkdf(shared, ascii("cloudlink/auth/v1"))
        val enc = hkdf(shared, ascii("cloudlink/enc/v1"))
        val sasN = ByteBuffer.wrap(hkdf(shared, ascii("cloudlink/sas/v1"), 4)).int.toLong() and 0xFFFFFFFFL
        return SessionKeys(auth, enc, "%06d".format(sasN % 1_000_000L))
    }

    fun sha256(vararg parts: ByteArray): ByteArray {
        val md = MessageDigest.getInstance("SHA-256")
        for (p in parts) md.update(p)
        return md.digest()
    }

    fun authHash(authKey: ByteArray): String = sha256(authKey).toHex()

    // ---- AES-256-GCM ----
    private fun gcm(mode: Int, key: ByteArray, nonce: ByteArray, aad: ByteArray, data: ByteArray): ByteArray {
        val c = Cipher.getInstance("AES/GCM/NoPadding")
        c.init(mode, SecretKeySpec(key, "AES"), GCMParameterSpec(128, nonce))
        c.updateAAD(aad)
        return c.doFinal(data)
    }

    // ---- control messages ----
    fun sealCtl(encKey: ByteArray, json: String, nonce: ByteArray = randomBytes(12)): String {
        val ct = gcm(Cipher.ENCRYPT_MODE, encKey, nonce, CTL_AAD, json.toByteArray(Charsets.UTF_8))
        return Base64.getEncoder().encodeToString(nonce + ct)
    }

    /** Returns the decrypted JSON text; throws on tampering / short input. */
    fun openCtl(encKey: ByteArray, blob: String): String {
        val raw = Base64.getDecoder().decode(blob)
        require(raw.size >= 12 + 16) { "short blob" }
        return String(gcm(Cipher.DECRYPT_MODE, encKey, raw.copyOfRange(0, 12), CTL_AAD, raw.copyOfRange(12, raw.size)), Charsets.UTF_8)
    }

    // ---- file chunks ----
    fun xferKey(encKey: ByteArray, tid: ByteArray): ByteArray = hkdf(encKey, ascii("cloudlink/xfer/v1") + tid)

    private fun u32(n: Int): ByteArray = ByteBuffer.allocate(4).putInt(n).array()

    private fun nonce(seq: Int): ByteArray = ByteArray(8) + u32(seq)

    fun sealChunk(key: ByteArray, tid: ByteArray, seq: Int, plain: ByteArray): ByteArray {
        val hdr = byteArrayOf(FRAME_VERSION.toByte()) + tid + u32(seq)
        return hdr + gcm(Cipher.ENCRYPT_MODE, key, nonce(seq), tid + u32(seq), plain)
    }

    class Frame(val tid: ByteArray, val seq: Int, val ct: ByteArray)

    fun parseFrame(frame: ByteArray): Frame {
        if (frame.size < FRAME_HEADER + 16 || frame[0].toInt() != FRAME_VERSION) throw IllegalArgumentException("bad frame")
        return Frame(frame.copyOfRange(1, 17), ByteBuffer.wrap(frame, 17, 4).int, frame.copyOfRange(21, frame.size))
    }

    fun openChunk(key: ByteArray, tid: ByteArray, seq: Int, ct: ByteArray): ByteArray =
        gcm(Cipher.DECRYPT_MODE, key, nonce(seq), tid + u32(seq), ct)

    // ---- LAN v2: pairing commitment + session auth ----
    fun pairCommit(pub: ByteArray, nonce: ByteArray): String = sha256(ascii("cloudlink/pair/v2"), pub, nonce).toHex()

    /** HMAC-SHA256 over u16BE len(label)|label then each part as u16BE len|bytes. */
    fun mac(key: ByteArray, label: ByteArray, vararg parts: ByteArray): ByteArray {
        val m = Mac.getInstance("HmacSHA256")
        m.init(SecretKeySpec(key, "HmacSHA256"))
        fun lp(b: ByteArray) {
            m.update(byteArrayOf((b.size ushr 8).toByte(), b.size.toByte()))
            m.update(b)
        }
        lp(label)
        for (p in parts) lp(p)
        return m.doFinal()
    }

    fun sessionKey(encKey: ByteArray, nc: ByteArray, ns: ByteArray): ByteArray =
        hkdf(encKey, ascii("cloudlink/sess/v2") + nc + ns)
}
