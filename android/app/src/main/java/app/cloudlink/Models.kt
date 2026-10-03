package app.cloudlink

data class PeerInfo(
    val id: String, val name: String, val paired: Boolean, val online: Boolean, val host: String, val port: Int,
)

data class Entry(val name: String, val isDir: Boolean, val size: Long, val mtime: Long)

data class TransferInfo(
    val peerId: String, val tid: String, val name: String, val size: Long, val done: Long,
    val direction: String,           // "in" | "out"
    val state: String,               // active | done | failed | cancelled
    val error: String? = null, val path: String? = null,
) {
    val finished get() = state != "active"
}

sealed interface Pairing {
    data object Idle : Pairing
    data class Connecting(val host: String) : Pairing
    data class Request(val peerName: String, val host: String) : Pairing   // we are the responder: accept?
    data class Verify(val sas: String, val peerName: String) : Pairing     // both: compare the 6 digits
    data class Done(val peerName: String) : Pairing
    data class Failed(val reason: String) : Pairing
}
