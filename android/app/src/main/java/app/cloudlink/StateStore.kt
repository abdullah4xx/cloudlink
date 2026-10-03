package app.cloudlink

import org.json.JSONObject
import java.io.File
import java.util.UUID

/** Persistent app state (private app storage). Same fields as linux_app/core/state.py. */
class StateStore(private val file: File, defaultName: String) {
    data class Peer(val name: String, val authKey: String, val encKey: String, val host: String, val port: Int)

    @Volatile var deviceId: String = ""
    @Volatile var deviceName: String = defaultName
    @Volatile var port: Int = 47616
    @Volatile var shareRoot: String = ""
    @Volatile var allowUploads: Boolean = true
    private val peers = LinkedHashMap<String, Peer>()

    init {
        try {
            val o = JSONObject(file.readText())
            deviceId = o.optString("device_id", "")
            o.optString("device_name", "").takeIf { it.isNotBlank() }?.let { deviceName = it }
            o.optInt("port", 47616).takeIf { it in 0..65535 }?.let { port = it }
            shareRoot = o.optString("share_root", "")
            allowUploads = o.optBoolean("allow_uploads", true)
            o.optJSONObject("peers")?.let { ps ->
                for (id in ps.keys()) {
                    val p = ps.getJSONObject(id)
                    peers[id] = Peer(p.optString("name"), p.getString("auth_key"), p.getString("enc_key"),
                        p.optString("host"), p.optInt("port", 0))
                }
            }
        } catch (_: Exception) {
        }
        if (deviceId.isEmpty()) {
            deviceId = "andr-" + UUID.randomUUID().toString().replace("-", "").take(16)
            save()
        }
    }

    @Synchronized fun peer(id: String): Peer? = peers[id]
    @Synchronized fun allPeers(): Map<String, Peer> = LinkedHashMap(peers)

    @Synchronized fun addPeer(id: String, name: String, authKey: ByteArray, encKey: ByteArray, host: String, port: Int) {
        peers[id] = Peer(name, authKey.toHex(), encKey.toHex(), host, port)
        save()
    }

    @Synchronized fun removePeer(id: String) {
        if (peers.remove(id) != null) save()
    }

    @Synchronized fun update(block: StateStore.() -> Unit) {
        block()
        save()
    }

    @Synchronized fun save() {
        val ps = JSONObject()
        for ((id, p) in peers) {
            ps.put(id, JSONObject().put("name", p.name).put("auth_key", p.authKey).put("enc_key", p.encKey)
                .put("host", p.host).put("port", p.port))
        }
        val o = JSONObject().put("device_id", deviceId).put("device_name", deviceName).put("port", port)
            .put("share_root", shareRoot).put("allow_uploads", allowUploads).put("peers", ps)
        file.parentFile?.mkdirs()
        val tmp = File(file.path + ".tmp")
        tmp.writeText(o.toString())
        if (!tmp.renameTo(file)) { file.delete(); tmp.renameTo(file) }
    }
}
