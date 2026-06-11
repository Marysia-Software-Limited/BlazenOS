//! `blazend-audio-in` — microphone capture, ring buffer, VAD feed.
//!
//! M1 skeleton: brings up the IPC publisher, accepts `--mock` to emit
//! synthetic audio frames for the dev-host launcher. Real `cpal` capture
//! lands in M2.

use std::time::Duration;

use blazend_ipc::{Event, EventEnvelope, Publisher, runtime_dir};
use clap::Parser;

#[derive(Parser, Debug)]
#[command(name = "blazend-audio-in", version)]
struct Args {
    /// Emit synthetic frames instead of touching real hardware.
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

    let args = Args::parse();
    let publisher = Publisher::bind(runtime_dir().join("audio-in.sock")).await?;
    tracing::info!(socket = ?publisher.socket_path, "audio-in online");

    let mut ts_ms: u64 = 0;
    loop {
        if args.mock {
            publisher
                .publish(EventEnvelope::new(
                    "blazend-audio-in",
                    ts_ms,
                    Event::SystemEvent {
                        kind: "tick".into(),
                        detail: Some(format!("mock frame {ts_ms}")),
                    },
                ))
                .await?;
        }
        tokio::time::sleep(Duration::from_millis(1000)).await;
        ts_ms += 1000;
    }
}
