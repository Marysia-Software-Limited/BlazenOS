//! `blazend-audio-out` — speaker playback.
//!
//! Real mode (M4): opens a `cpal` output stream on the configured device
//! (temporarily HDMI — `vc4hdmi0` — since the ReSpeaker HAT has no speaker
//! attached), subscribes to `tts.frame` from `blazend-tts`, reads the
//! just-synthesised utterance's PCM from the shared-memory TTS ring
//! (`tts-ring.shm`), resamples it to the device rate and plays it. With no
//! usable output device it drops frames (like `--mock`). The mic path stays on
//! the ReSpeaker HAT (`blazend-audio-in`); only output is HDMI.

use std::collections::VecDeque;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use anyhow::{anyhow, Result};
use blazend_audioring::{LinearResampler, RingReader};
use blazend_ipc::{runtime_dir, Event, EventEnvelope, Publisher, Subscriber};
use clap::Parser;
use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use cpal::SampleFormat;

#[derive(Parser, Debug)]
#[command(name = "blazend-audio-out", version)]
struct Args {
    /// Drop incoming TTS frames instead of touching audio hardware.
    #[arg(long)]
    mock: bool,
    /// Output device name substring to prefer (e.g. `vc4hdmi0` for HDMI).
    #[arg(long, default_value = "default")]
    device: String,
}

/// Mono playback queue (device sample rate) drained by the cpal callback.
type Queue = Arc<Mutex<VecDeque<f32>>>;

fn ring_path() -> PathBuf {
    runtime_dir().join("tts-ring.shm")
}

fn pick_output_device(host: &cpal::Host, want: &str) -> Option<cpal::Device> {
    let want = want.trim().to_lowercase();
    if !want.is_empty() && want != "default" {
        if let Ok(devices) = host.output_devices() {
            for device in devices {
                let matches = device
                    .name()
                    .map(|n| n.to_lowercase().contains(&want))
                    .unwrap_or(false);
                if matches && device.default_output_config().is_ok() {
                    return Some(device);
                }
            }
        }
    }
    if let Some(device) = host.default_output_device() {
        if device.default_output_config().is_ok() {
            return Some(device);
        }
    }
    host.output_devices()
        .ok()?
        .find(|d| d.default_output_config().is_ok())
}

fn choose_output_config(device: &cpal::Device) -> Result<cpal::SupportedStreamConfig> {
    let ranges: Vec<cpal::SupportedStreamConfigRange> =
        device.supported_output_configs()?.collect();
    // Prefer a format we can write (F32, then I16) at a common rate — HDMI's
    // default config may be I8, which we don't emit.
    for fmt in [SampleFormat::F32, SampleFormat::I16] {
        for rate in [48_000u32, 44_100, 22_050, 16_000] {
            let sr = cpal::SampleRate(rate);
            if let Some(range) = ranges.iter().find(|r| {
                r.sample_format() == fmt && r.min_sample_rate() <= sr && sr <= r.max_sample_rate()
            }) {
                return Ok((*range).with_sample_rate(sr));
            }
        }
    }
    Ok(device.default_output_config()?)
}

fn build_output_stream(
    device: &cpal::Device,
    config: &cpal::StreamConfig,
    sample_format: SampleFormat,
    channels: usize,
    queue: Queue,
) -> Result<cpal::Stream> {
    let err_fn = |e| tracing::error!("cpal output stream error: {e}");
    let stream = match sample_format {
        SampleFormat::F32 => device.build_output_stream(
            config,
            move |data: &mut [f32], _: &_| {
                let mut q = queue.lock().unwrap();
                for frame in data.chunks_mut(channels) {
                    let s = q.pop_front().unwrap_or(0.0);
                    frame.iter_mut().for_each(|x| *x = s);
                }
            },
            err_fn,
            None,
        )?,
        SampleFormat::I16 => device.build_output_stream(
            config,
            move |data: &mut [i16], _: &_| {
                let mut q = queue.lock().unwrap();
                for frame in data.chunks_mut(channels) {
                    let s = (q.pop_front().unwrap_or(0.0).clamp(-1.0, 1.0) * 32767.0) as i16;
                    frame.iter_mut().for_each(|x| *x = s);
                }
            },
            err_fn,
            None,
        )?,
        other => return Err(anyhow!("unsupported cpal sample format {other:?}")),
    };
    Ok(stream)
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

async fn connect(sock: PathBuf) -> Subscriber {
    loop {
        if sock.exists() {
            if let Ok(s) = Subscriber::connect(&sock).await {
                return s;
            }
        }
        tokio::time::sleep(Duration::from_millis(200)).await;
    }
}

async fn run_real(args: &Args) -> Result<()> {
    let host = cpal::default_host();
    let device = pick_output_device(&host, &args.device)
        .ok_or_else(|| anyhow!("no usable output device"))?;
    let supported = choose_output_config(&device)?;
    let sample_format = supported.sample_format();
    let out_rate = supported.sample_rate().0;
    let channels = supported.channels() as usize;
    let config: cpal::StreamConfig = supported.into();
    let name = device.name().unwrap_or_else(|_| "unknown".into());
    tracing::info!(%name, out_rate, channels, ?sample_format, "audio-out playback ready");

    let queue: Queue = Arc::new(Mutex::new(VecDeque::new()));
    let stream = build_output_stream(&device, &config, sample_format, channels, queue.clone())?;
    stream.play()?;

    let ring = open_ring(ring_path()).await;
    let mut sub = connect(runtime_dir().join("tts.sock")).await;
    tracing::info!(
        ring_rate = ring.sample_rate,
        out_rate,
        "subscribed to tts.frame"
    );

    while let Some(env) = sub.next().await? {
        if let Event::TtsFrame { samples, voice } = env.event {
            let end = ring.write_pos();
            let pcm = ring.read_range(end.saturating_sub(samples as u64), end);
            let mono: Vec<f32> = pcm.iter().map(|&s| s as f32 / 32768.0).collect();
            let mut resampler = LinearResampler::new(ring.sample_rate, out_rate);
            let mut out = Vec::new();
            resampler.process(&mono, &mut out);
            let played = out.len();
            queue.lock().unwrap().extend(out);
            tracing::info!(%voice, samples, played, "playing tts");
        }
    }
    Ok(())
}

#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()),
        )
        .init();
    let args = Args::parse();
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

    if args.mock {
        loop {
            tokio::time::sleep(Duration::from_secs(60)).await;
        }
    }

    match run_real(&args).await {
        Ok(()) => Ok(()),
        Err(e) => {
            tracing::warn!("no output device ({e}) — dropping TTS frames");
            loop {
                tokio::time::sleep(Duration::from_secs(60)).await;
            }
        }
    }
}
