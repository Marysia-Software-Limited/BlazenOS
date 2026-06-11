package os.blazen.jessica.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import os.blazen.jessica.R

@Composable
fun HomeScreen(intentCount: Long) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(24.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(text = stringResource(R.string.home_greeting))
        Text(text = stringResource(R.string.home_status_intents, intentCount))
        Text(text = stringResource(R.string.home_listen_hint))
    }
}

@Preview(showBackground = true, locale = "pl")
@Composable
private fun HomeScreenPreviewPl() {
    HomeScreen(intentCount = 12)
}

@Preview(showBackground = true, locale = "en")
@Composable
private fun HomeScreenPreviewEn() {
    HomeScreen(intentCount = 12)
}
