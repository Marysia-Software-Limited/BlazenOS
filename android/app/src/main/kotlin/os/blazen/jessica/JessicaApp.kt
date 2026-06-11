package os.blazen.jessica

import android.app.Application
import os.blazen.jessica.core.JessicaCore

class JessicaApp : Application() {
    val core: JessicaCore by lazy { JessicaCore.create() }

    override fun onCreate() {
        super.onCreate()
        val yaml = assets.open("intents-system.yaml").bufferedReader().use { it.readText() }
        runCatching { core.loadIntents(yaml) }
    }
}
