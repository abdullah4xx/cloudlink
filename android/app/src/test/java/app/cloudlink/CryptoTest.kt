package app.cloudlink

import org.json.JSONObject
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

/** The Kotlin crypto must match the Python reference byte-for-byte (protocol/test_vectors.json). */
class CryptoTest {
    private val v = JSONObject(javaClass.getResource("/test_vectors.json")!!.readText())
    private fun hex(k: String) = v.getString(k).hexToBytes()
    private val w = v.getJSONObject("v2")
    private fun whex(k: String) = w.getString(k).hexToBytes()

    @Test fun x25519PublicKeys() {
        assertEquals(v.getString("pubA"), Crypto.publicFromPrivate(hex("privA")).toHex())
        assertEquals(v.getString("pubB"), Crypto.publicFromPrivate(hex("privB")).toHex())
    }

    @Test fun sharedSecretBothDirections() {
        assertEquals(v.getString("shared"), Crypto.sharedSecret(hex("privA"), hex("pubB")).toHex())
        assertEquals(v.getString("shared"), Crypto.sharedSecret(hex("privB"), hex("pubA")).toHex())
    }

    @Test fun lowOrderPointRejected() {
        assertThrows(IllegalArgumentException::class.java) { Crypto.sharedSecret(hex("privA"), ByteArray(32)) }
    }

    @Test fun deriveSession() {
        val k = Crypto.deriveSession(hex("shared"))
        assertEquals(v.getString("authKey"), k.authKey.toHex())
        assertEquals(v.getString("encKey"), k.encKey.toHex())
        assertEquals(v.getString("sas"), k.sas)
        assertEquals(v.getString("authHash"), Crypto.authHash(k.authKey))
    }

    @Test fun controlMessage() {
        val blob = Crypto.sealCtl(hex("encKey"), v.getString("ctlJson"), hex("tid").copyOf(12))
        assertEquals(v.getString("ctlBlob"), blob)
        assertEquals(v.getString("ctlJson"), Crypto.openCtl(hex("encKey"), v.getString("ctlBlob")))
    }

    @Test fun tamperedControlMessageRejected() {
        val raw = java.util.Base64.getDecoder().decode(v.getString("ctlBlob"))
        raw[raw.size - 1] = (raw[raw.size - 1].toInt() xor 1).toByte()
        assertThrows(Exception::class.java) { Crypto.openCtl(hex("encKey"), java.util.Base64.getEncoder().encodeToString(raw)) }
    }

    @Test fun fileChunk() {
        val tid = hex("tid")
        val key = Crypto.xferKey(hex("encKey"), tid)
        assertEquals(v.getString("xferKey"), key.toHex())
        val seq = v.getInt("chunkSeq")
        val frame = Crypto.sealChunk(key, tid, seq, v.getString("chunkPlain").toByteArray())
        assertEquals(v.getString("chunkFrame"), frame.toHex())
        val f = Crypto.parseFrame(hex("chunkFrame"))
        assertArrayEquals(tid, f.tid)
        assertEquals(seq, f.seq)
        assertEquals(v.getString("chunkPlain"), String(Crypto.openChunk(key, f.tid, f.seq, f.ct)))
    }

    @Test fun chunkWithWrongSeqRejected() {
        val tid = hex("tid"); val key = Crypto.xferKey(hex("encKey"), tid)
        val f = Crypto.parseFrame(hex("chunkFrame"))
        assertThrows(Exception::class.java) { Crypto.openChunk(key, tid, f.seq + 1, f.ct) }
    }

    @Test fun pairingCommitment() {
        assertEquals(w.getString("pairCommit"), Crypto.pairCommit(hex("pubA"), whex("pairNonce")))
    }

    @Test fun sessionHandshakeMacsAndKey() {
        val auth = hex("authKey")
        val idC = w.getString("idClient").toByteArray(); val idS = w.getString("idServer").toByteArray()
        val nc = whex("ncHex"); val ns = whex("nsHex")
        assertEquals(w.getString("macSrv"), Crypto.mac(auth, "srv".toByteArray(), idC, idS, nc, ns).toHex())
        assertEquals(w.getString("macCli"), Crypto.mac(auth, "cli".toByteArray(), idC, idS, nc, ns).toHex())
        assertEquals(w.getString("sessionKey"), Crypto.sessionKey(hex("encKey"), nc, ns).toHex())
    }

    @Test fun freshKeypairsAgree() {
        val (pa, qa) = Crypto.generateKeypair(); val (pb, qb) = Crypto.generateKeypair()
        assertArrayEquals(Crypto.sharedSecret(pa, qb), Crypto.sharedSecret(pb, qa))
        assertTrue(Crypto.deriveSession(Crypto.sharedSecret(pa, qb)).sas.matches(Regex("\\d{6}")))
    }

    @Test fun pathHelpers() {
        val root = kotlin.io.path.createTempDirectory("cl").toFile()
        java.io.File(root, "a").mkdirs()
        assertEquals(java.io.File(root, "a").canonicalFile, resolveUnder(root, "/a"))
        assertEquals(null, resolveUnder(root, "../etc"))
        assertEquals(null, resolveUnder(root, "a/../../x"))
        assertEquals("file", sanitizeName(".."))
        assertEquals("x.txt", sanitizeName("../../x.txt"))
        root.deleteRecursively()
    }
}
