//! `blazend-wake` — wake-word detector.
//!
//! Real mode (M3): runs the trained "Hej Jessico / Hey Jessica" openWakeWord
//! model (`owww::WakeModel`, a 3-stage ONNX pipeline via `ort`) over a rolling
//! window of the shared-memory input ring (`audio-ring.shm`) and publishes
//! `wake.detected` when the score clears the threshold (with a cooldown). The
//! onnxruntime shared library is loaded dynamically — set `ORT_DYLIB_PATH` (the
//! venv ships one). With no model / no dylib it falls back to `--mock`.

mod owww;

use std::path::PathBuf;
use std::time::{Duration, Instant};

use anyhow::Result;
use blazend_audioring::RingReader;
use blazend_ipc::{runtime_dir, Event, EventEnvelope, Publisher};
use clap::Parser;
use owww::WakeModel;

const SR: usize = 16_000;

#[derive(Parser, Debug)]
#[command(name = "blazend-wake", version)]
struct Args {
    /// Emit a synthetic wake every `--mock-period` seconds instead of listening.
    #[arg(long)]
    mock: bool,
    #[arg(long, default_value_t = 30)]
    mock_period_s: u64,
    /// Model id reported in `wake.detected`.
    #[arg(long, default_value = "jessica")]
    model: String,
    /// Wake score threshold.
    #[arg(long, default_value_t = 0.5)]
    threshold: f32,
    /// Minimum gap between wakes (ms).
    #[arg(long, default_value_t = 1500)]
    cooldown_ms: u64,
    #[arg(long, default_value = "models/wake/melspectrogram.onnx")]
    melspec: PathBuf,
    #[arg(long, default_value = "models/wake/embedding_model.onnx")]
    embedding: PathBuf,
    #[arg(long, default_value = "models/wake/jessica.onnx")]
    classifier: PathBuf,
    /// Debug: score one raw mono-16k-i16 file (e.g. `piper --output-raw`) and exit.
    #[arg(long)]
    score_raw: Option<PathBuf>,
}

async fn open_ring(path: PathBuf) -> RingReader {
    loop {
        if path.exists() {
            if let Ok(r) = RingReader::open(&path) {
                return r;
            }
        }
        tokio::time::sleep(Duration::from_millis(200)).await;
    }
}

async fn mock_loop(publisher: &Publisher, args: &Args) -> Result<()> {
    let mut ts: u64 = 0;
    loop {
        tokio::time::sleep(Duration::from_secs(args.mock_period_s)).await;
        ts += args.mock_period_s * 1000;
        publisher
            .publish(EventEnvelope::new(
                "blazend-wake",
                ts,
                Event::WakeDetected {
                    model: args.model.clone(),
                    score: 0.9,
                    language: "pl".into(),
                },
            ))
            .await?;
        tracing::info!(model = %args.model, "mock wake.detected");
    }
}

async fn run_real(publisher: &Publisher, args: &Args) -> Result<()> {
    let mut model = WakeModel::load(&args.melspec, &args.embedding, &args.classifier)?;
    tracing::info!(
        threshold = args.threshold,
        "wake model loaded; listening on the input ring"
    );
    let ring = open_ring(runtime_dir().join("audio-ring.shm")).await;

    let buf_cap = SR * 5 / 2; // 2.5 s rolling window
    let mut buffer: Vec<i16> = Vec::new();
    let mut last_pos = ring.write_pos();
    let mut cooldown_until = Instant::now();

    loop {
        tokio::time::sleep(Duration::from_millis(200)).await;
        let pos = ring.write_pos();
        if pos > last_pos {
            buffer.extend(ring.read_range(last_pos, pos));
            last_pos = pos;
            if buffer.len() > buf_cap {
                buffer.drain(0..buffer.len() - buf_cap);
            }
        }
        if buffer.len() < SR {
            continue;
        }
        let score = model.score(&buffer)?;
        tracing::debug!(score, "scored window");
        if score >= args.threshold && Instant::now() >= cooldown_until {
            cooldown_until = Instant::now() + Duration::from_millis(args.cooldown_ms);
            let ts = last_pos * 1000 / SR as u64;
            tracing::info!(score, "wake.detected");
            publisher
                .publish(EventEnvelope::new(
                    "blazend-wake",
                    ts,
                    Event::WakeDetected {
                        model: args.model.clone(),
                        score,
                        language: "pl".into(),
                    },
                ))
                .await?;
        }
    }
}

#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()),
        )
        .init();
    let args = Args::parse();

    if let Some(path) = &args.score_raw {
        let bytes = std::fs::read(path)?;
        let audio: Vec<i16> = bytes
            .chunks_exact(2)
            .map(|b| i16::from_le_bytes([b[0], b[1]]))
            .collect();
        let mut model = WakeModel::load(&args.melspec, &args.embedding, &args.classifier)?;
        println!("score = {:.4}", model.score(&audio)?);
        return Ok(());
    }

    let publisher = Publisher::bind(runtime_dir().join("wake.sock")).await?;
    tracing::info!(socket = ?publisher.socket_path, "wake online");

    if args.mock {
        return mock_loop(&publisher, &args).await;
    }
    match run_real(&publisher, &args).await {
        Ok(()) => Ok(()),
        Err(e) => {
            tracing::warn!("wake model unavailable ({e}) — falling back to mock");
            mock_loop(&publisher, &args).await
        }
    }
}
