package os.blazen.jessica

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import os.blazen.jessica.ui.HomeScreen
import os.blazen.jessica.ui.PermissionGate
import os.blazen.jessica.ui.theme.JessicaTheme

class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val app = application as JessicaApp
        val orchestrator = app.orchestrator

        setContent {
            JessicaTheme {
                Surface(
                    modifier = Modifier,
                    color = MaterialTheme.colorScheme.background,
                ) {
                    PermissionGate {
                        val state by orchestrator.state.collectAsState()
                        val language by orchestrator.language.collectAsState()
                        val pinned by orchestrator.isLanguagePinned.collectAsState()
                        val lastTurn by orchestrator.lastTurn.collectAsState()
                        HomeScreen(
                            state = state,
                            language = language,
                            isPinned = pinned,
                            lastTurn = lastTurn,
                            intentCount = app.core.intentCount(),
                            onTap = orchestrator::tap,
                            onPinLanguage = orchestrator::pinLanguage,
                            onUnpinLanguage = orchestrator::unpinLanguage,
                        )
                    }
                }
            }
        }
    }

    override fun onStop() {
        // Belt-and-suspenders: cancel any in-flight ASR/TTS when the user
        // leaves the screen so the mic and speaker free up.
        (application as JessicaApp).orchestrator.interrupt()
        super.onStop()
    }
}
