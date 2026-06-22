//! `blazend-audio-in` — microphone capture, shared-memory ring buffer, VAD feed.
//!
//! Real mode (M2): opens the ReSpeaker/WM8960 HAT capture PCM via the **`alsa`
//! crate with RW (`readi`) access** — deliberately *not* cpal/mmap, because
//! cpal locks both PCM substreams of the i2s card and blocks playback on the
//! same HAT (the TTS speaker). RW capture leaves the playback substream free,
//! so the HAT runs **full-duplex** (mic in + Jessica's voice out on one card),
//! exactly like `arecord` + `aplay`. We open `plughw:…` so the codec's native
//! rate/channels are converted to **mono-ish 16 kHz** (we capture stereo and
//! downmix), write PCM into the shared ring (`runtime_dir()/audio-ring.shm`)
//! that the Python ASR reads, and run an energy VAD (`vad.rs`) publishing
//! `vad.start` / `vad.end` on `audio-in.sock`. A periodic `system.event`
//! heartbeat keeps `blazend-health` from declaring mic starvation. With no
//! device (WSL/CI, or no HAT) it emits `mic.absent` and falls back to
//! synthetic frames so the rest of the stack still has a heartbeat.

mod vad;

use std::thread;
use std::time::Duration;

use anyhow::{anyhow, Context, Result};
use blazend_audioring::RingWriter;
use blazend_ipc::{runtime_dir, Event, EventEnvelope, Publisher};
use clap::Parser;
use vad::{EnergyVad, VadEvent};

/// ASR-facing capture rate; the ring always stores mono 16 kHz i16.
const TARGET_RATE: u32 = 16_000;
/// WM8960 HAT exposes the two analog mics as a stereo capture; we downmix.
const CAPTURE_CHANNELS: u32 = 2;

#[derive(Parser, Debug)]
#[command(name = "blazend-audio-in", version)]
struct Args {
    /// Emit synthetic frames instead of touching real hardware.
    #[arg(long)]
    mock: bool,
    /// ALSA capture PCM. A `plughw:…` name is RW-shareable, so the HAT stays
    /// full-duplex (TTS can play on the same card while we capture). A bare
    /// token is wrapped as `plughw:CARD=<token>,DEV=0`.
    #[arg(long, default_value = "plughw:CARD=wm8960soundcard,DEV=0")]
    device: String,
    /// Ring buffer length in seconds (mirrors `audio.yaml input.ring_buffer_seconds`).
    #[arg(long, default_value_t = 3)]
    ring_seconds: u32,
    /// VAD frame size in milliseconds (`audio.yaml input.frame_ms`).
    #[arg(long, default_value_t = 20)]
    frame_ms: u32,
    /// VAD open threshold (linear i16 RMS) — speech starts above this.
    #[arg(long, default_value_t = 1800.0)]
    open_rms: f32,
    /// VAD close threshold (linear i16 RMS) — silence below this.
    #[arg(long, default_value_t = 1100.0)]
    close_rms: f32,
    /// VAD open multiplier over the learned ambient noise floor.
    #[arg(long, default_value_t = 2.5)]
    open_mult: f32,
    /// VAD close multiplier over the learned ambient noise floor.
    #[arg(long, default_value_t = 1.6)]
    close_mult: f32,
    /// Trailing silence before an utterance ends (ms).
    #[arg(long, default_value_t = 300)]
    hangover_ms: u32,
    /// Minimum speech before an utterance opens (ms).
    #[arg(long, default_value_t = 150)]
    min_speech_ms: u32,
}

fn ring_path() -> std::path::PathBuf {
    runtime_dir().join("audio-ring.shm")
}

/// Resolve a `--device` value to an ALSA PCM name. Full names (`…:…`) and
/// `default` pass through; a bare token becomes a shareable `plughw` PCM.
fn alsa_device(d: &str) -> String {
    let d = d.trim();
    if d.is_empty() || d == "default" || d.contains(':') {
        if d.is_empty() {
            "default".to_string()
        } else {
            d.to_string()
        }
    } else {
        format!("plughw:CARD={d},DEV=0")
    }
}

fn f32_to_i16(x: f32) -> i16 {
    (x.clamp(-1.0, 1.0) * 32767.0) as i16
}

/// ms of audio captured so far — a monotonic, clock-free `ts_ms`.
fn ts_from_pos(write_pos: u64) -> u64 {
    write_pos * 1000 / TARGET_RATE as u64
}

/// Open the capture PCM with **RW interleaved** access (not mmap) so the card's
/// playback substream stays free for TTS. Requests mono-downmix-friendly
/// stereo at the target rate; `plughw` converts from the codec's native clock.
fn open_capture(device: &str, channels: u32) -> Result<alsa::pcm::PCM> {
    use alsa::pcm::{Access, Format, HwParams, PCM};
    use alsa::{Direction, ValueOr};
    let pcm = PCM::new(device, Direction::Capture, false)
        .with_context(|| format!("open ALSA capture {device}"))?;
    {
        let hwp = HwParams::any(&pcm)?;
        hwp.set_channels(channels)?;
        hwp.set_rate(TARGET_RATE, ValueOr::Nearest)?;
        hwp.set_format(Format::s16())?;
        hwp.set_access(Access::RWInterleaved)?;
        let buf = (i64::from(TARGET_RATE) / 2).max(2048); // ~0.5 s
        hwp.set_buffer_size_near(buf)?;
        hwp.set_period_size_near((buf / 4).max(256), ValueOr::Nearest)?;
        pcm.hw_params(&hwp)?;
    }
    pcm.prepare()?;
    Ok(pcm)
}

async fn run_capture(publisher: &Publisher, args: &Args) -> Result<()> {
    let device = alsa_device(&args.device);
    let channels = CAPTURE_CHANNELS as usize;

    // Open the PCM inside the capture thread (PCM isn't Sync); report the open
    // result back so a failure falls through to the synthetic mock loop.
    let (ready_tx, ready_rx) = std::sync::mpsc::channel::<Result<(), String>>();
    let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel::<Vec<f32>>();
    let dev = device.clone();
    thread::Builder::new()
        .name("alsa-capture".into())
        .spawn(move || {
            let pcm = match open_capture(&dev, channels as u32) {
                Ok(p) => {
                    let _ = ready_tx.send(Ok(()));
                    p
                }
                Err(e) => {
                    let _ = ready_tx.send(Err(format!("{e:#}")));
                    return;
                }
            };
            let io = match pcm.io_i16() {
                Ok(io) => io,
                Err(e) => {
                    tracing::error!("ALSA io_i16: {e}");
                    return;
                }
            };
            let period = (TARGET_RATE as usize / 10).max(160); // ~100 ms
            let mut buf = vec![0i16; period * channels];
            loop {
                match io.readi(&mut buf) {
                    Ok(n) if n > 0 => {
                        let mut mono = Vec::with_capacity(n);
                        for frame in buf[..n * channels].chunks(channels) {
                            let sum: i32 = frame.iter().map(|&s| i32::from(s)).sum();
                            mono.push((sum / channels as i32) as f32 / 32768.0);
                        }
                        if tx.send(mono).is_err() {
                            break; // consumer gone
                        }
                    }
                    Ok(_) => {}
                    Err(e) => {
                        if pcm.try_recover(e, true).is_err() {
                            tracing::error!("ALSA capture unrecoverable; stopping");
                            break;
                        }
                    }
                }
            }
        })?;

    match ready_rx.recv() {
        Ok(Ok(())) => {}
        Ok(Err(e)) => return Err(anyhow!("ALSA capture open failed: {e}")),
        Err(_) => return Err(anyhow!("capture thread exited before opening")),
    }
    tracing::info!(%device, rate = TARGET_RATE, channels, "microphone capture starting (ALSA RW)");

    publisher
        .publish(EventEnvelope::new(
            "blazend-audio-in",
            0,
            Event::SystemEvent {
                kind: "mic.ready".into(),
                detail: Some(format!("{device} → 16kHz mono (RW, full-duplex)")),
            },
        ))
        .await?;

    let capacity = TARGET_RATE * args.ring_seconds;
    let mut ring = RingWriter::create(ring_path(), TARGET_RATE, 1, capacity)?;
    let mut vad = EnergyVad::new(
        args.open_rms,
        args.close_rms,
        args.open_mult,
        args.close_mult,
        args.hangover_ms,
        args.min_speech_ms,
        args.frame_ms,
    );
    let frame_size = (TARGET_RATE * args.frame_ms / 1000).max(1) as usize;
    tracing::info!(ring = ?ring_path(), "ring buffer live; listening");

    let mut frame_acc: Vec<i16> = Vec::with_capacity(frame_size);
    let mut last_heartbeat_pos: u64 = 0;

    // The capturer already delivers mono 16 kHz (plughw converted), so no
    // resample step is needed — convert to i16 and feed the ring + VAD.
    while let Some(mono) = rx.recv().await {
        let i16v: Vec<i16> = mono.iter().map(|&x| f32_to_i16(x)).collect();
        ring.push(&i16v);

        for &sample in &i16v {
            frame_acc.push(sample);
            if frame_acc.len() == frame_size {
                if let Some(event) = vad.push_frame(&frame_acc) {
                    let ts = ts_from_pos(ring.write_pos());
                    match event {
                        VadEvent::Start => {
                            tracing::info!("vad.start");
                            publisher
                                .publish(EventEnvelope::new(
                                    "blazend-audio-in",
                                    ts,
                                    Event::VadStart,
                                ))
                                .await?;
                        }
                        VadEvent::End { duration_ms } => {
                            tracing::info!(duration_ms, "vad.end");
                            publisher
                                .publish(EventEnvelope::new(
                                    "blazend-audio-in",
                                    ts,
                                    Event::VadEnd { duration_ms },
                                ))
                                .await?;
                        }
                    }
                }
                frame_acc.clear();
            }
        }

        // ~1 s heartbeat so blazend-health doesn't flag mic starvation in silence.
        let pos = ring.write_pos();
        if pos - last_heartbeat_pos >= TARGET_RATE as u64 {
            last_heartbeat_pos = pos;
            tracing::debug!(noise_floor = vad.noise_floor(), "heartbeat");
            publisher
                .publish(EventEnvelope::new(
                    "blazend-audio-in",
                    ts_from_pos(pos),
                    Event::SystemEvent {
                        kind: "tick".into(),
                        detail: None,
                    },
                ))
                .await?;
        }
    }
    Ok(())
}

/// No real device: keep the ring + a heartbeat + a periodic synthetic utterance
/// so the downstream pipeline is exercisable headless (CI / WSL).
async fn mock_loop(publisher: &Publisher, args: &Args) -> Result<()> {
    let capacity = TARGET_RATE * args.ring_seconds;
    let mut ring = RingWriter::create(ring_path(), TARGET_RATE, 1, capacity)?;
    let mut ts: u64 = 0;
    loop {
        // Heartbeat every second for ~15 s, then a synthetic 1 s "utterance".
        for _ in 0..15 {
            tokio::time::sleep(Duration::from_secs(1)).await;
            ts += 1000;
            publisher
                .publish(EventEnvelope::new(
                    "blazend-audio-in",
                    ts,
                    Event::SystemEvent {
                        kind: "tick".into(),
                        detail: Some(format!("mock frame {ts}")),
                    },
                ))
                .await?;
        }
        let tone: Vec<i16> = (0..TARGET_RATE)
            .map(|i| {
                let t = i as f32 / TARGET_RATE as f32;
                ((t * 440.0 * std::f32::consts::TAU).sin() * 8000.0) as i16
            })
            .collect();
        ring.push(&tone);
        publisher
            .publish(EventEnvelope::new("blazend-audio-in", ts, Event::VadStart))
            .await?;
        tokio::time::sleep(Duration::from_millis(50)).await;
        publisher
            .publish(EventEnvelope::new(
                "blazend-audio-in",
                ts,
                Event::VadEnd { duration_ms: 1000 },
            ))
            .await?;
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
    let publisher = Publisher::bind(runtime_dir().join("audio-in.sock")).await?;
    tracing::info!(socket = ?publisher.socket_path, "audio-in online");

    if args.mock {
        return mock_loop(&publisher, &args).await;
    }

    match run_capture(&publisher, &args).await {
        Ok(()) => Ok(()),
        Err(e) => {
            tracing::warn!(
                "no usable capture ({e}) (no mic/HAT attached, or WSL/CI) — falling back to synthetic frames"
            );
            publisher
                .publish(EventEnvelope::new(
                    "blazend-audio-in",
                    0,
                    Event::SystemEvent {
                        kind: "mic.absent".into(),
                        detail: Some("no usable ALSA capture device".into()),
                    },
                ))
                .await?;
            mock_loop(&publisher, &args).await
        }
    }
}
