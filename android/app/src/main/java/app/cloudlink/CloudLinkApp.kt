package app.cloudlink

import android.app.Application
import android.os.Build
import android.os.Environment
import java.io.File

class CloudLinkApp : Application() {
    lateinit var engine: Engine
        private set

    override fun onCreate() {
        super.onCreate()
        val defaultName = (Build.MODEL ?: "Android").take(48)
        val state = StateStore(File(filesDir, "state.json"), defaultName)
        engine = Engine(
            state = state,
            downloadDir = File(Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS), "CloudLink"),
            shareRootProvider = { shareRoot(state) },
        )
    }

    companion object {
        /** Everything on shared storage by default (needs "All files access"), or the folder chosen in Settings. */
        fun shareRoot(state: StateStore): File =
            state.shareRoot.takeIf { it.isNotBlank() }?.let(::File)?.takeIf { it.isDirectory }
                ?: Environment.getExternalStorageDirectory()

        fun hasAllFilesAccess(): Boolean =
            if (Build.VERSION.SDK_INT >= 30) Environment.isExternalStorageManager() else true
    }
}
