package os.blazen.jessica.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import os.blazen.jessica.R
import os.blazen.jessica.voice.JessicaState
import os.blazen.jessica.voice.Turn

/**
 * Visual contract:
 *   - One large mic button. Tap to start a turn, tap again to interrupt.
 *   - State badge above it (Idle / Listening / Thinking / Speaking / Error).
 *   - Last interaction below it (transcript + reply).
 *   - Language toggle (PL / EN) at the bottom.
 */
@Composable
fun HomeScreen(
    state: JessicaState,
    language: String,
    isPinned: Boolean,
    lastTurn: Turn?,
    intentCount: Long,
    onTap: () -> Unit,
    onPinLanguage: (String) -> Unit,
    onUnpinLanguage: () -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(24.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(
            text = stringResource(R.string.home_greeting),
            style = MaterialTheme.typography.titleLarge,
        )
        Text(
            text = stringResource(R.string.home_status_intents, intentCount),
            style = MaterialTheme.typography.bodyMedium,
        )
        Spacer(Modifier.height(12.dp))
        StateBadge(state = state)
        MicButton(state = state, onTap = onTap)
        Spacer(Modifier.height(8.dp))
        LastTurnPanel(lastTurn = lastTurn)
        Spacer(Modifier.weight(1f))
        LanguageToggle(
            language = language,
            isPinned = isPinned,
            onPinLanguage = onPinLanguage,
            onUnpinLanguage = onUnpinLanguage,
        )
    }
}

@Composable
private fun StateBadge(state: JessicaState) {
    val (key, tone) = when (state) {
        JessicaState.Idle -> R.string.state_idle to MaterialTheme.colorScheme.onSurfaceVariant
        is JessicaState.Listening -> R.string.state_listening to MaterialTheme.colorScheme.primary
        is JessicaState.Thinking -> R.string.state_thinking to MaterialTheme.colorScheme.tertiary
        is JessicaState.Speaking -> R.string.state_speaking to MaterialTheme.colorScheme.primary
        is JessicaState.Error -> R.string.state_error to MaterialTheme.colorScheme.error
    }
    Text(
        text = stringResource(key),
        color = tone,
        style = MaterialTheme.typography.titleMedium,
        fontWeight = FontWeight.SemiBold,
    )
    if (state is JessicaState.Listening && state.partial.isNotBlank()) {
        Text(
            text = state.partial,
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    } else if (state is JessicaState.Error) {
        Text(
            text = state.reason,
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.error,
        )
    }
}

@Composable
private fun MicButton(state: JessicaState, onTap: () -> Unit) {
    val labelRes = when (state) {
        JessicaState.Idle -> R.string.button_listen
        is JessicaState.Listening -> R.string.button_cancel
        is JessicaState.Speaking -> R.string.button_interrupt
        is JessicaState.Thinking, is JessicaState.Error -> R.string.button_busy
    }
    val container = when (state) {
        is JessicaState.Listening -> MaterialTheme.colorScheme.primary
        is JessicaState.Speaking -> MaterialTheme.colorScheme.secondary
        else -> MaterialTheme.colorScheme.primaryContainer
    }
    Surface(
        shape = CircleShape,
        color = container,
        modifier = Modifier.size(160.dp),
    ) {
        Box(contentAlignment = Alignment.Center) {
            Button(
                onClick = onTap,
                shape = CircleShape,
                colors = ButtonDefaults.buttonColors(containerColor = container),
                enabled = state !is JessicaState.Thinking,
                modifier = Modifier.size(160.dp),
            ) {
                Text(stringResource(labelRes))
            }
        }
    }
}

@Composable
private fun LastTurnPanel(lastTurn: Turn?) {
    if (lastTurn == null) {
        Text(
            text = stringResource(R.string.home_listen_hint),
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            style = MaterialTheme.typography.bodyMedium,
        )
        return
    }
    Column(
        modifier = Modifier.fillMaxWidth(),
        verticalArrangement = Arrangement.spacedBy(4.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(
            text = stringResource(R.string.last_turn_you, lastTurn.transcript),
            style = MaterialTheme.typography.bodyMedium,
        )
        Text(
            text = stringResource(R.string.last_turn_jessica, lastTurn.reply),
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.primary,
        )
    }
}

@Composable
private fun LanguageToggle(
    language: String,
    isPinned: Boolean,
    onPinLanguage: (String) -> Unit,
    onUnpinLanguage: () -> Unit,
) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Text(
            text = stringResource(
                if (isPinned) R.string.language_pinned else R.string.language_auto,
                language.uppercase(),
            ),
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Row(
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            TextButton(onClick = { onPinLanguage("pl") }) {
                Text(stringResource(R.string.language_pl))
            }
            TextButton(onClick = { onPinLanguage("en") }) {
                Text(stringResource(R.string.language_en))
            }
            TextButton(onClick = onUnpinLanguage) {
                Text(stringResource(R.string.language_auto_button))
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Previews
// ---------------------------------------------------------------------------

@Preview(showBackground = true, locale = "pl")
@Composable
private fun HomeScreenPreviewPlIdle() {
    HomeScreen(
        state = JessicaState.Idle,
        language = "pl",
        isPinned = false,
        lastTurn = null,
        intentCount = 8,
        onTap = {},
        onPinLanguage = {},
        onUnpinLanguage = {},
    )
}

@Preview(showBackground = true, locale = "en")
@Composable
private fun HomeScreenPreviewEnTurn() {
    HomeScreen(
        state = JessicaState.Idle,
        language = "en",
        isPinned = true,
        lastTurn = Turn("what time is it", "Let me check the time for you.", "en"),
        intentCount = 8,
        onTap = {},
        onPinLanguage = {},
        onUnpinLanguage = {},
    )
}
