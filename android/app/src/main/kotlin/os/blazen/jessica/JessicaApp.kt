package os.blazen.jessica

import android.app.Application
import os.blazen.jessica.core.JessicaCore
import os.blazen.jessica.voice.JessicaOrchestrator

class JessicaApp : Application() {

    val core: JessicaCore by lazy { JessicaCore.create() }

    /**
     * Process-wide orchestrator. Lazy so unit tests that spin up the
     * application class without an Android system service stack can
     * avoid constructing TTS/SpeechRecognizer.
     */
    val orchestrator: JessicaOrchestrator by lazy {
        JessicaOrchestrator(this, core)
    }

    override fun onCreate() {
        super.onCreate()
        val yaml = assets.open("intents-system.yaml").bufferedReader().use { it.readText() }
        runCatching { core.loadIntents(yaml) }
    }

    override fun onTerminate() {
        orchestrator.shutdown()
        core.close()
        super.onTerminate()
    }
}
