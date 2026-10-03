package app.cloudlink

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
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
import java.net.Inet4Address
import java.net.InetSocketAddress
import java.net.NetworkInterface
import java.util.concurrent.ConcurrentHashMap

/**
 * UDP broadcast beacons (port 47615): {"app":"cloudlink","v":2,"id","name","port"} every 2 s, expire after 8 s.
 * Unauthenticated hint only — trust comes from pairing / the session handshake.
 *
 * When a [context] is given (and no explicit [targets], i.e. not in loopback tests) the device also registers and
 * browses `_cloudlink._tcp` over mDNS via [NsdManager] — routers that drop broadcasts still allow multicast DNS.
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
    private val context: Context? = null,
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

    class Seen(val id: String, val name: String, val host: String, val port: Int, val seenAt: Long, val sticky: Boolean = false)

    val peers = ConcurrentHashMap<String, Seen>()
    private var socket: DatagramSocket? = null
    private var jobs = emptyList<Job>()
    private var nsd: Nsd? = null

    fun start(scope: CoroutineScope) {
        // mDNS first, so a busy UDP port never disables it
        val ctx = context
        if (ctx != null && targets == null) {
            try {
                nsd = Nsd(ctx, myId, myName(), tcpPort(), ::ingest, ::drop).also { it.start() }
            } catch (_: Exception) {
                // UDP beacons still work
            }
        }
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
        nsd?.stop()
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
        ingest(pid, m.optString("name").take(64), host, port, false)
    }

    /** mDNS peers are sticky: they stay until the service is lost (no periodic beacons to refresh them). */
    private fun ingest(pid: String, name: String, host: String, port: Int, sticky: Boolean) {
        val old = peers[pid]
        peers[pid] = Seen(pid, name, host, port, System.currentTimeMillis(), sticky || old?.sticky == true)
        if (old == null || old.name != name || old.host != host || old.port != port) onChange()
    }

    private fun drop(pid: String) {
        if (peers.remove(pid) != null) onChange()
    }

    private suspend fun beaconLoop(s: DatagramSocket, dst: List<InetSocketAddress>) {
        while (currentCoroutineContext().isActive && !s.isClosed) {
            val data = JSONObject().put("app", "cloudlink").put("v", 2).put("id", myId)
                .put("name", myName()).put("port", tcpPort()).toString().toByteArray(Charsets.UTF_8)
            for (t in dst) {
                try { s.send(DatagramPacket(data, data.size, t)) } catch (_: Exception) {}
            }
            val now = System.currentTimeMillis()
            val gone = peers.values.filter { !it.sticky && now - it.seenAt > ttlMs }
            for (g in gone) peers.remove(g.id)
            if (gone.isNotEmpty()) onChange()
            delay(intervalMs)
        }
    }

    /** mDNS / DNS-SD through the platform NsdManager (no extra library). */
    private class Nsd(
        ctx: Context, private val myId: String, private val myName: String, private val port: Int,
        private val onFound: (String, String, String, Int, Boolean) -> Unit, private val onLost: (String) -> Unit,
    ) {
        private val mgr = ctx.applicationContext.getSystemService(Context.NSD_SERVICE) as NsdManager
        private val type = "_cloudlink._tcp."
        private var ownName = "CloudLink-" + myId.filter { it.isLetterOrDigit() }.take(12)
        private val names = ConcurrentHashMap<String, String>()   // service name -> peer id
        private val queue = java.util.ArrayDeque<NsdServiceInfo>()
        private var resolving = false
        private var registered: NsdManager.RegistrationListener? = null
        private var discovery: NsdManager.DiscoveryListener? = null

        fun start() {
            val info = NsdServiceInfo().apply {
                serviceName = ownName; serviceType = type; port = this@Nsd.port
                setAttribute("id", myId); setAttribute("name", myName.take(60)); setAttribute("v", "2")
            }
            registered = object : NsdManager.RegistrationListener {
                override fun onServiceRegistered(i: NsdServiceInfo) { ownName = i.serviceName }
                override fun onRegistrationFailed(i: NsdServiceInfo, e: Int) {}
                override fun onServiceUnregistered(i: NsdServiceInfo) {}
                override fun onUnregistrationFailed(i: NsdServiceInfo, e: Int) {}
            }
            mgr.registerService(info, NsdManager.PROTOCOL_DNS_SD, registered)
            discovery = object : NsdManager.DiscoveryListener {
                override fun onDiscoveryStarted(t: String) {}
                override fun onDiscoveryStopped(t: String) {}
                override fun onStartDiscoveryFailed(t: String, e: Int) {}
                override fun onStopDiscoveryFailed(t: String, e: Int) {}
                override fun onServiceFound(s: NsdServiceInfo) {
                    if (s.serviceName == ownName) return
                    enqueue(s)
                }
                override fun onServiceLost(s: NsdServiceInfo) {
                    names.remove(s.serviceName)?.let(onLost)
                }
            }
            mgr.discoverServices(type, NsdManager.PROTOCOL_DNS_SD, discovery)
        }

        fun stop() {
            try { discovery?.let { mgr.stopServiceDiscovery(it) } } catch (_: Exception) {}
            try { registered?.let { mgr.unregisterService(it) } } catch (_: Exception) {}
        }

        // NsdManager can resolve only one service at a time, so resolve serially.
        @Synchronized private fun enqueue(s: NsdServiceInfo) { queue.add(s); pump() }

        @Synchronized private fun pump() {
            if (resolving) return
            val next = queue.poll() ?: return
            resolving = true
            @Suppress("DEPRECATION")
            mgr.resolveService(next, object : NsdManager.ResolveListener {
                override fun onResolveFailed(i: NsdServiceInfo, e: Int) { done() }
                override fun onServiceResolved(i: NsdServiceInfo) {
                    try {
                        val pid = i.attributes["id"]?.toString(Charsets.UTF_8).orEmpty()
                        val name = i.attributes["name"]?.toString(Charsets.UTF_8).orEmpty().take(64)
                        val host = (i.host as? Inet4Address)?.hostAddress
                        if (pid.isNotEmpty() && pid != myId && host != null && i.port in 1..65535) {
                            names[i.serviceName] = pid
                            onFound(pid, name, host, i.port, true)
                        }
                    } finally { done() }
                }
            })
        }

        @Synchronized private fun done() { resolving = false; pump() }
    }
}
