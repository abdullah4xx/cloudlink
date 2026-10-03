package app.cloudlink

import java.io.File

class LanException(message: String) : Exception(message)

fun ByteArray.toHex(): String = joinToString("") { "%02x".format(it) }

fun String.hexToBytes(): ByteArray {
    if (length % 2 != 0) throw LanException("bad_handshake")
    return try {
        ByteArray(length / 2) { i -> substring(2 * i, 2 * i + 2).toInt(16).toByte() }
    } catch (e: NumberFormatException) {
        throw LanException("bad_handshake")
    }
}

/** Same rules as peer.py: only the last path component, no control chars, never empty/./.. */
fun sanitizeName(name: String): String {
    var n = name.replace('\\', '/').substringAfterLast('/')
    n = n.filter { it.code >= 0x20 }.trim()
    if (n.isEmpty() || n == "." || n == "..") return "file"
    return if (n.length > 200) n.substring(0, 200) else n
}

fun uniquePath(dir: File, name: String): File {
    var p = File(dir, name)
    if (!p.exists() && !File(p.path + ".part").exists()) return p
    val dot = name.lastIndexOf('.')
    val (stem, suffix) = if (dot > 0) name.substring(0, dot) to name.substring(dot) else name to ""
    var i = 1
    while (true) {
        p = File(dir, "$stem ($i)$suffix")
        if (!p.exists() && !File(p.path + ".part").exists()) return p
        i++
    }
}

/** Resolve [rel] under [root]; null if it escapes (.., symlinks). */
fun resolveUnder(root: File, rel: String): File? {
    val r = root.canonicalFile
    val p = File(r, rel.trimStart('/')).canonicalFile
    return if (p == r || p.path.startsWith(r.path + File.separator)) p else null
}
