package app.cloudlink

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.NetworkInterface
import java.util.concurrent.ConcurrentHashMap

/**
 * UDP broadcast beacons (port 47615): {"app":"cloudlink","v":2,"id","name","port"} every 2 s, expire after 8 s.
 * Unauthenticated hint only — trust comes from pairing / the session handshake.
 */
class Discovery(
    private val myId: String,
    private val myName: () -> String,
    private val tcpPort: () -> Int,
    private val onChange: () -> Unit,
    private val listenPort: Int = PORT,
    private val targets: List<InetSocketAddress>? = null,
    private val intervalMs: Long = 2000,
    private val ttlMs: Long = 8000,
) {
    companion object {
        const val PORT = 47615

        /** 255.255.255.255 plus each interface's directed broadcast (many APs forward only the latter). */
        fun defaultTargets(): List<InetSocketAddress> {
            val out = LinkedHashSet<InetSocketAddress>()
            out += InetSocketAddress(InetAddress.getByName("255.255.255.255"), PORT)
            try {
                for (ni in NetworkInterface.getNetworkInterfaces()) {
                    if (!ni.isUp || ni.isLoopback) continue
                    for (ia in ni.interfaceAddresses) ia.broadcast?.let { out += InetSocketAddress(it, PORT) }
                }
            } catch (_: Exception) {
            }
            return out.toList()
        }
    }

    class Seen(val id: String, val name: String, val host: String, val port: Int, val seenAt: Long)

    val peers = ConcurrentHashMap<String, Seen>()
    private var socket: DatagramSocket? = null
    private var jobs = emptyList<Job>()

    fun start(scope: CoroutineScope) {
        val s = DatagramSocket(null)
        s.reuseAddress = true
        s.broadcast = true
        s.bind(InetSocketAddress(listenPort))
        socket = s
        val dst = targets ?: defaultTargets()
        jobs = listOf(
            scope.launch { receiveLoop(s) },
            scope.launch { beaconLoop(s, dst) },
        )
    }

    fun stop() {
        jobs.forEach { it.cancel() }
        try { socket?.close() } catch (_: Exception) {}
    }

    private fun receiveLoop(s: DatagramSocket) {
        val buf = ByteArray(2048)
        while (!s.isClosed) {
            try {
                val pkt = DatagramPacket(buf, buf.size)
                s.receive(pkt)
                onDatagram(String(pkt.data, 0, pkt.length, Charsets.UTF_8), pkt.address.hostAddress ?: continue)
            } catch (e: Exception) {
                if (s.isClosed) return
            }
        }
    }

    private fun onDatagram(text: String, host: String) {
        val m = try { JSONObject(text) } catch (e: Exception) { return }
        if (m.optString("app") != "cloudlink" || m.optInt("v", -1) != 2) return
        val pid = m.optString("id")
        val port = m.optInt("port", 0)
        if (pid.isEmpty() || pid == myId || port !in 1..65535) return
        val name = m.optString("name").take(64)
        val old = peers[pid]
        peers[pid] = Seen(pid, name, host, port, System.currentTimeMillis())
        if (old == null || old.name != name || old.host != host || old.port != port) onChange()
    }

    private suspend fun beaconLoop(s: DatagramSocket, dst: List<InetSocketAddress>) {
        while (currentCoroutineContext().isActive && !s.isClosed) {
            val data = JSONObject().put("app", "cloudlink").put("v", 2).put("id", myId)
                .put("name", myName()).put("port", tcpPort()).toString().toByteArray(Charsets.UTF_8)
            for (t in dst) {
                try { s.send(DatagramPacket(data, data.size, t)) } catch (_: Exception) {}
            }
            val now = System.currentTimeMillis()
            val gone = peers.values.filter { now - it.seenAt > ttlMs }
            for (g in gone) peers.remove(g.id)
            if (gone.isNotEmpty()) onChange()
            delay(intervalMs)
        }
    }
}
