//! `blazend-wake` — wake-word detector loop.
//!
//! M1 skeleton: in `--mock` mode emits a synthetic `wake.detected` every
//! 30 s so the rest of the stack has a heartbeat to react to. Real ONNX
//! Runtime inference over openWakeWord lands in M2.

use std::time::Duration;

use blazend_ipc::{runtime_dir, Event, EventEnvelope, Publisher};
use clap::Parser;

#[derive(Parser, Debug)]
#[command(name = "blazend-wake", version)]
struct Args {
    /// Emit a synthetic wake every `--mock-period` seconds.
    #[arg(long)]
    mock: bool,
    #[arg(long, default_value_t = 30)]
    mock_period_s: u64,
    /// Wake-word models to "load" (informational in mock mode).
    #[arg(long, default_value = "hey_blazen_en,hey_blazen_pl")]
    models: String,
}

#[tokio::main(flavor = "current_thread")]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()),
        )
        .init();
    let args = Args::parse();
    let publisher = Publisher::bind(runtime_dir().join("wake.sock")).await?;
    let models: Vec<String> = args
        .models
        .split(',')
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .collect();
    tracing::info!(?models, socket = ?publisher.socket_path, "wake online");

    let mut ts_ms: u64 = 0;
    let mut idx: usize = 0;
    loop {
        tokio::time::sleep(Duration::from_secs(if args.mock {
            args.mock_period_s
        } else {
            60
        }))
        .await;
        ts_ms += args.mock_period_s.saturating_mul(1000);
        if args.mock && !models.is_empty() {
            let model = models[idx % models.len()].clone();
            let language = if model.ends_with("_pl") { "pl" } else { "en" }.to_string();
            publisher
                .publish(EventEnvelope::new(
                    "blazend-wake",
                    ts_ms,
                    Event::WakeDetected {
                        model,
                        score: 0.75,
                        language,
                    },
                ))
                .await?;
            idx += 1;
        }
    }
}
