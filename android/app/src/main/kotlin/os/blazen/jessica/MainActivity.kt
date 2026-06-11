package os.blazen.jessica

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.ui.Modifier
import os.blazen.jessica.ui.HomeScreen
import os.blazen.jessica.ui.theme.JessicaTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val core = (application as JessicaApp).core
        setContent {
            JessicaTheme {
                Surface(
                    modifier = Modifier,
                    color = MaterialTheme.colorScheme.background,
                ) {
                    HomeScreen(intentCount = core.intentCount())
                }
            }
        }
    }
}
