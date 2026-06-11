//! `blazend-tts` — Piper synthesis worker. M1 stub: emits a tts.frame
//! envelope per "synthesised" sentence; real `piper-rs` integration in M4.

use std::time::Duration;

use blazend_ipc::{Event, EventEnvelope, Publisher, runtime_dir};
use clap::Parser;

#[derive(Parser, Debug)]
#[command(name = "blazend-tts", version)]
struct Args {
    #[arg(long)]
    mock: bool,
}

#[tokio::main(flavor = "current_thread")]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "info".into()),
        )
        .init();
    let _ = Args::parse();
    let publisher = Publisher::bind(runtime_dir().join("tts.sock")).await?;
    publisher
        .publish(EventEnvelope::new(
            "blazend-tts",
            0,
            Event::SystemEvent { kind: "ready".into(), detail: None },
        ))
        .await?;
    tracing::info!(socket = ?publisher.socket_path, "tts online");
    loop {
        tokio::time::sleep(Duration::from_secs(60)).await;
    }
}
