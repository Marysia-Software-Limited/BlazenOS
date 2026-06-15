//! `blazend-audio-in` — microphone capture, shared-memory ring buffer, VAD feed.
//!
//! Real mode (M2): opens the default `cpal` input (the ReSpeaker 2-Mics HAT —
//! WM8960, ALSA card `wm8960soundcard`), downmixes to mono + resamples to
//! 16 kHz, writes PCM into the shared-memory ring (`ring.rs`,
//! `runtime_dir()/audio-ring.shm`) that the Python ASR reads, and runs an
//! energy VAD (`vad.rs`) that publishes `vad.start` / `vad.end` on
//! `audio-in.sock`. A periodic `system.event` heartbeat keeps `blazend-health`
//! from declaring mic starvation during silence. With no input device (WSL/CI,
//! or no HAT) it emits `mic.absent` and falls back to synthetic frames so the
//! rest of the stack still has a heartbeat.

mod ring;
mod vad;

use std::time::Duration;

use anyhow::{anyhow, Result};
use blazend_ipc::{runtime_dir, Event, EventEnvelope, Publisher};
use clap::Parser;
use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use cpal::SampleFormat;
use ring::RingWriter;
use vad::{EnergyVad, VadEvent};

/// ASR-facing capture rate; the ring always stores mono 16 kHz i16.
const TARGET_RATE: u32 = 16_000;

#[derive(Parser, Debug)]
#[command(name = "blazend-audio-in", version)]
struct Args {
    /// Emit synthetic frames instead of touching real hardware.
    #[arg(long)]
    mock: bool,
    /// Input device name substring to prefer (e.g. `wm8960` for the ReSpeaker
    /// HAT). Empty / `default` uses the ALSA default, then the first working
    /// input — so a USB mic or HAT works even when the ALSA default has no
    /// capture slave.
    #[arg(long, default_value = "default")]
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

/// Streaming linear resampler (mono f32). Carries state across cpal buffers.
struct LinearResampler {
    step: f64, // input samples advanced per output sample
    pos: f64,
    prev: f32,
    primed: bool,
}

impl LinearResampler {
    fn new(in_rate: u32, out_rate: u32) -> Self {
        Self {
            step: in_rate as f64 / out_rate as f64,
            pos: 0.0,
            prev: 0.0,
            primed: false,
        }
    }

    fn process(&mut self, input: &[f32], out: &mut Vec<f32>) {
        for &cur in input {
            if !self.primed {
                self.prev = cur;
                self.primed = true;
                continue;
            }
            while self.pos < 1.0 {
                let y = self.prev + (cur - self.prev) * self.pos as f32;
                out.push(y);
                self.pos += self.step;
            }
            self.pos -= 1.0;
            self.prev = cur;
        }
    }
}

fn downmix_to_mono_f32(interleaved: &[f32], channels: usize, out: &mut Vec<f32>) {
    if channels <= 1 {
        out.extend_from_slice(interleaved);
        return;
    }
    for chunk in interleaved.chunks(channels) {
        out.push(chunk.iter().sum::<f32>() / channels as f32);
    }
}

fn build_input_stream(
    device: &cpal::Device,
    config: &cpal::StreamConfig,
    sample_format: SampleFormat,
    channels: usize,
    tx: tokio::sync::mpsc::UnboundedSender<Vec<f32>>,
) -> Result<cpal::Stream> {
    let err_fn = |e| tracing::error!("cpal input stream error: {e}");
    let stream = match sample_format {
        SampleFormat::F32 => device.build_input_stream(
            config,
            move |data: &[f32], _: &_| {
                let mut mono = Vec::with_capacity(data.len() / channels.max(1));
                downmix_to_mono_f32(data, channels, &mut mono);
                let _ = tx.send(mono);
            },
            err_fn,
            None,
        )?,
        SampleFormat::I16 => device.build_input_stream(
            config,
            move |data: &[i16], _: &_| {
                let mut mono = Vec::with_capacity(data.len() / channels.max(1));
                for chunk in data.chunks(channels) {
                    let sum: i32 = chunk.iter().map(|&s| s as i32).sum();
                    mono.push((sum / channels as i32) as f32 / 32768.0);
                }
                let _ = tx.send(mono);
            },
            err_fn,
            None,
        )?,
        other => return Err(anyhow!("unsupported cpal sample format {other:?}")),
    };
    Ok(stream)
}

fn f32_to_i16(x: f32) -> i16 {
    (x.clamp(-1.0, 1.0) * 32767.0) as i16
}

/// ms of audio captured so far — a monotonic, clock-free `ts_ms`.
fn ts_from_pos(write_pos: u64) -> u64 {
    write_pos * 1000 / TARGET_RATE as u64
}

/// Pick a usable input device: a name match (`--device`) first, then the ALSA
/// default, then the first device that yields a config. Necessary because the
/// ALSA "default" often has no capture slave even with a HAT/USB mic attached.
fn pick_input_device(host: &cpal::Host, want: &str) -> Option<cpal::Device> {
    let want = want.trim().to_lowercase();
    if !want.is_empty() && want != "default" {
        if let Ok(devices) = host.input_devices() {
            for device in devices {
                let matches = device
                    .name()
                    .map(|n| n.to_lowercase().contains(&want))
                    .unwrap_or(false);
                if matches && device.default_input_config().is_ok() {
                    return Some(device);
                }
            }
        }
    }
    if let Some(device) = host.default_input_device() {
        if device.default_input_config().is_ok() {
            return Some(device);
        }
    }
    host.input_devices()
        .ok()?
        .find(|d| d.default_input_config().is_ok())
}

/// Choose a concrete input config, preferring rates the codec clocks cleanly
/// (16 kHz first — our target, no resample). cpal's `default_input_config`
/// can report a rate the hardware doesn't actually deliver (e.g. 44100 on the
/// WM8960), which yields a silent stream.
fn choose_input_config(device: &cpal::Device) -> Result<cpal::SupportedStreamConfig> {
    let ranges: Vec<cpal::SupportedStreamConfigRange> = device.supported_input_configs()?.collect();
    for rate in [16_000u32, 48_000, 32_000, 8_000] {
        let sr = cpal::SampleRate(rate);
        if let Some(range) = ranges
            .iter()
            .find(|r| r.min_sample_rate() <= sr && sr <= r.max_sample_rate())
        {
            return Ok((*range).with_sample_rate(sr));
        }
    }
    Ok(device.default_input_config()?)
}

async fn run_capture(publisher: &Publisher, args: &Args) -> Result<()> {
    let host = cpal::default_host();
    let device =
        pick_input_device(&host, &args.device).ok_or_else(|| anyhow!("no usable input device"))?;
    let supported = choose_input_config(&device)?;
    let sample_format = supported.sample_format();
    let in_rate = supported.sample_rate().0;
    let in_channels = supported.channels() as usize;
    let config: cpal::StreamConfig = supported.into();
    let name = device.name().unwrap_or_else(|_| "unknown".into());
    tracing::info!(%name, in_rate, in_channels, ?sample_format, "microphone capture starting");

    publisher
        .publish(EventEnvelope::new(
            "blazend-audio-in",
            0,
            Event::SystemEvent {
                kind: "mic.ready".into(),
                detail: Some(format!("{name} @ {in_rate}Hz x{in_channels} → 16kHz mono")),
            },
        ))
        .await?;

    let capacity = TARGET_RATE * args.ring_seconds;
    let mut ring = RingWriter::create(ring_path(), TARGET_RATE, 1, capacity)?;
    let mut vad = EnergyVad::new(
        args.open_rms,
        args.close_rms,
        args.hangover_ms,
        args.min_speech_ms,
        args.frame_ms,
    );
    let frame_size = (TARGET_RATE * args.frame_ms / 1000).max(1) as usize;
    let mut resampler = LinearResampler::new(in_rate, TARGET_RATE);

    let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel::<Vec<f32>>();
    let stream = build_input_stream(&device, &config, sample_format, in_channels, tx)?;
    stream.play()?;
    tracing::info!(ring = ?ring_path(), "ring buffer live; listening");

    let mut frame_acc: Vec<i16> = Vec::with_capacity(frame_size);
    let mut resampled: Vec<f32> = Vec::new();
    let mut last_heartbeat_pos: u64 = 0;

    while let Some(mono) = rx.recv().await {
        resampled.clear();
        resampler.process(&mono, &mut resampled);
        let i16v: Vec<i16> = resampled.iter().map(|&x| f32_to_i16(x)).collect();
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
                "no input device ({e}) (no mic/HAT attached, or WSL/CI) — falling back to synthetic frames"
            );
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
            mock_loop(&publisher, &args).await
        }
    }
}
