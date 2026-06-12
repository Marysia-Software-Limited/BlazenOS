//! `blazend-audio-in` — microphone capture, ring buffer, VAD feed.
//!
//! M2: in normal mode this probes the real default input device via `cpal`
//! and reports it (`mic.ready` / `mic.absent`). When no device is present
//! (WSL / CI) it falls back to synthetic frames so the rest of the stack
//! still has a heartbeat. The streaming capture → shared-memory ring buffer
//! that `blazend-wake`/VAD consume lands on real hardware (it needs a live
//! ALSA device to validate).

use std::time::Duration;

use blazend_ipc::{Event, EventEnvelope, Publisher, runtime_dir};
use clap::Parser;
use cpal::traits::{DeviceTrait, HostTrait};

#[derive(Parser, Debug)]
#[command(name = "blazend-audio-in", version)]
struct Args {
    /// Emit synthetic frames instead of touching real hardware.
    #[arg(long)]
    mock: bool,
}

/// Probe the default input device. Returns `(name, sample_rate, channels)`.
fn probe_mic() -> Option<(String, u32, u16)> {
    let host = cpal::default_host();
    let device = host.default_input_device()?;
    let name = device.name().unwrap_or_else(|_| "unknown".into());
    let cfg = device.default_input_config().ok()?;
    Some((name, cfg.sample_rate().0, cfg.channels()))
}

async fn mock_loop(publisher: &Publisher) -> anyhow::Result<()> {
    let mut ts_ms: u64 = 0;
    loop {
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
        tokio::time::sleep(Duration::from_millis(1000)).await;
        ts_ms += 1000;
    }
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

    if args.mock {
        return mock_loop(&publisher).await;
    }

    match probe_mic() {
        Some((name, sample_rate, channels)) => {
            tracing::info!(%name, sample_rate, channels, "microphone detected");
            publisher
                .publish(EventEnvelope::new(
                    "blazend-audio-in",
                    0,
                    Event::SystemEvent {
                        kind: "mic.ready".into(),
                        detail: Some(format!("{name} @ {sample_rate}Hz x{channels}")),
                    },
                ))
                .await?;
            // Real capture → ring buffer is the on-device step (needs a live
            // ALSA device to validate). Keep the unit up so peers can attach.
            tracing::info!("real cpal capture → ring buffer is the on-hardware step (M2/M3)");
            std::future::pending::<()>().await;
            Ok(())
        }
        None => {
            tracing::warn!("no input device (WSL/CI?) — falling back to synthetic frames");
            publisher
                .publish(EventEnvelope::new(
                    "blazend-audio-in",
                    0,
                    Event::SystemEvent {
                        kind: "mic.absent".into(),
                        detail: Some("no default input device".into()),
                    },
                ))
                .await?;
            mock_loop(&publisher).await
        }
    }
}
