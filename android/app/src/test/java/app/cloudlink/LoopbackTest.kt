package app.cloudlink

import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import java.io.File

/** Two real engines over real TCP on 127.0.0.1: pairing (SAS), browse, get, put, and rejection of strangers. */
class LoopbackTest {
    private lateinit var tmp: File
    private lateinit var a: Engine
    private lateinit var b: Engine
    private lateinit var shareA: File
    private lateinit var shareB: File
    private lateinit var dlA: File

    private fun engine(name: String, share: File, dl: File): Engine {
        val st = StateStore(File(tmp, "$name.json"), name).also { it.port = 0 }
        return Engine(st, dl, { share }, beaconPort = 0, beaconTargets = emptyList(), listenHost = "127.0.0.1").also { it.start() }
    }

    @Before fun setUp() {
        tmp = kotlin.io.path.createTempDirectory("cl-loop").toFile()
        shareA = File(tmp, "shareA").apply { mkdirs() }
        shareB = File(tmp, "shareB").apply { mkdirs() }
        dlA = File(tmp, "dlA")
        a = engine("A", shareA, dlA)
        b = engine("B", shareB, File(tmp, "dlB"))
    }

    @After fun tearDown() { a.stop(); b.stop(); tmp.deleteRecursively() }

    private suspend fun <T> until(what: String, f: () -> T?): T = withTimeout(10_000) {
        while (true) { f()?.let { return@withTimeout it }; kotlinx.coroutines.delay(20) }
        @Suppress("UNREACHABLE_CODE") error(what)
    }

    private suspend fun pair() {
        a.startPairing("127.0.0.1", b.port)
        until("B request") { b.pairing.value as? Pairing.Request }
        b.answerPairing(true)
        val va = until("A verify") { a.pairing.value as? Pairing.Verify }
        val vb = until("B verify") { b.pairing.value as? Pairing.Verify }
        assertEquals(va.sas, vb.sas)
        a.answerPairing(true); b.answerPairing(true)
        until("A done") { a.pairing.value as? Pairing.Done }
        until("B done") { b.pairing.value as? Pairing.Done }
    }

    @Test fun pairBrowseGetPut() = runBlocking {
        File(shareB, "docs").mkdirs()
        File(shareB, "hello.txt").writeText("hello from B")
        File(shareB, ".secret").writeText("hidden")
        pair()
        val bid = b.state.deviceId

        val names = a.listDir(bid, "/").map { it.name }
        assertEquals(listOf("docs", "hello.txt"), names)          // dot-files hidden, dirs first

        a.download(bid, "/hello.txt")
        val got = until("download") { File(dlA, "hello.txt").takeIf { it.exists() } }
        assertEquals("hello from B", got.readText())

        val up = File(tmp, "up.bin").apply { writeBytes(ByteArray(200_000) { (it % 251).toByte() }) }
        assertTrue(a.upload(bid, up, "/docs"))
        assertEquals(up.readBytes().toList(), File(shareB, "docs/up.bin").readBytes().toList())
    }

    @Test fun traversalIsRefused() = runBlocking {
        pair()
        val bid = b.state.deviceId
        try { a.listDir(bid, "/../"); fail("should not list outside the share") } catch (e: Exception) { /* not_found */ }
        val up = File(tmp, "x.txt").apply { writeText("x") }
        assertTrue(!a.upload(bid, up, "/../../tmp"))
    }

    @Test fun unpairedPeerIsRejected() = runBlocking {
        pair()
        val aid = a.state.deviceId
        b.unpair(a.state.deviceId)          // B forgets A
        a.state.peer(b.state.deviceId)      // A still thinks it's paired
        try { a.listDir(b.state.deviceId, "/"); fail("B must reject an unknown device") } catch (e: EngineError) {
            assertTrue(e.message!!.contains("not_paired"))
        }
        assertTrue(aid.isNotEmpty())
    }

    @Test fun declinedPairingLeavesNothingBehind() = runBlocking {
        a.startPairing("127.0.0.1", b.port)
        until("B request") { b.pairing.value as? Pairing.Request }
        b.answerPairing(false)
        until("A failed") { a.pairing.value as? Pairing.Failed }
        assertTrue(a.state.allPeers().isEmpty() && b.state.allPeers().isEmpty())
    }
}
