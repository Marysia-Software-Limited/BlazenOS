//! `blazend-fabric` — Pi-side daemon for the Jessica fabric.
//!
//! M0 stub: bind a publisher socket, log "online", emit
//! `system.event` heartbeats. Pairing / mDNS / TLS-RPC land in M2+.

use std::time::Duration;

use blazend_ipc::{runtime_dir, Event, EventEnvelope, Publisher};
use clap::Parser;

#[derive(Parser, Debug)]
#[command(name = "blazend-fabric", version)]
struct Args {
    /// Skip pairing — useful for first-boot on a single-device fabric.
    #[arg(long)]
    standalone: bool,
}

#[tokio::main(flavor = "current_thread")]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()),
        )
        .init();
    let args = Args::parse();

    let publisher = Publisher::bind(runtime_dir().join("fabric.sock")).await?;
    publisher
        .publish(EventEnvelope::new(
            "blazend-fabric",
            0,
            Event::SystemEvent {
                kind: "ready".into(),
                detail: Some(if args.standalone {
                    "standalone fabric — no peers".into()
                } else {
                    "fabric subsystem online".into()
                }),
            },
        ))
        .await?;
    tracing::info!(socket = ?publisher.socket_path, standalone = args.standalone, "fabric online");

    let mut tick: u64 = 0;
    loop {
        tokio::time::sleep(Duration::from_secs(30)).await;
        tick += 30_000;
        publisher
            .publish(EventEnvelope::new(
                "blazend-fabric",
                tick,
                Event::SystemEvent {
                    kind: "heartbeat".into(),
                    detail: None,
                },
            ))
            .await?;
    }
}
