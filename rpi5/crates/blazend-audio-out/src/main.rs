//! `blazend-audio-out` — speaker playback + mixer.
//! M1 skeleton; real `cpal` playback lands in M4.

use std::time::Duration;

use blazend_ipc::{runtime_dir, Event, EventEnvelope, Publisher};
use clap::Parser;

#[derive(Parser, Debug)]
#[command(name = "blazend-audio-out", version)]
struct Args {
    /// Drop incoming TTS frames instead of touching audio hardware.
    #[arg(long)]
    mock: bool,
}

#[tokio::main(flavor = "current_thread")]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()),
        )
        .init();
    let _ = Args::parse();
    let publisher = Publisher::bind(runtime_dir().join("audio-out.sock")).await?;
    tracing::info!(socket = ?publisher.socket_path, "audio-out online");
    publisher
        .publish(EventEnvelope::new(
            "blazend-audio-out",
            0,
            Event::SystemEvent {
                kind: "ready".into(),
                detail: None,
            },
        ))
        .await?;
    loop {
        tokio::time::sleep(Duration::from_secs(60)).await;
    }
}
