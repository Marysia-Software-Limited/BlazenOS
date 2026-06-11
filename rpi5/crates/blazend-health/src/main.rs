//! `blazend-health` — watchdog + recovery-mode controller.
//!
//! Connects to every other unit's socket, watches for liveness, writes
//! `/run/blazen/state.json` periodically. M1 skeleton: just publishes
//! a heartbeat and reports a fake "all green" state.

use std::time::Duration;

use blazend_ipc::{Event, EventEnvelope, Publisher, runtime_dir};
use clap::Parser;
use serde_json::json;

#[derive(Parser, Debug)]
#[command(name = "blazend-health", version)]
struct Args {
    /// Don't actually attempt to peer-connect; just heartbeat.
    #[arg(long)]
    mock: bool,
    /// State file to write (default: /run/blazen/state.json or
    /// $BLAZEN_RUNTIME_DIR/state.json).
    #[arg(long)]
    state_path: Option<std::path::PathBuf>,
}

#[tokio::main(flavor = "current_thread")]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "info".into()),
        )
        .init();
    let args = Args::parse();
    let publisher = Publisher::bind(runtime_dir().join("health.sock")).await?;
    let state_path = args
        .state_path
        .unwrap_or_else(|| runtime_dir().join("state.json"));
    tracing::info!(socket = ?publisher.socket_path, state = ?state_path, "health online");

    let mut tick: u64 = 0;
    loop {
        let state = json!({
            "v": 1,
            "ts_ms": tick,
            "ready": true,
            "units": {
                "blazend-orchestrator": "running",
                "blazend-audio-in":     "running",
                "blazend-wake":         "running",
                "blazend-asr":          "running",
                "blazend-brain":        "running",
                "blazend-tts":          "running",
                "blazend-audio-out":    "running",
            },
            "hailo":  { "present": false },
            "ssh":    { "enabled": false },
            "led":    "green",
        });
        if let Some(parent) = state_path.parent() {
            tokio::fs::create_dir_all(parent).await.ok();
        }
        tokio::fs::write(&state_path, serde_json::to_vec_pretty(&state)?).await?;
        publisher
            .publish(EventEnvelope::new(
                "blazend-health",
                tick,
                Event::SystemEvent { kind: "heartbeat".into(), detail: None },
            ))
            .await?;
        tick += 5_000;
        tokio::time::sleep(Duration::from_secs(5)).await;
    }
}
