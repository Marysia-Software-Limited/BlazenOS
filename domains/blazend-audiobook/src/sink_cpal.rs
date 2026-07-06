//! A [`SinkFactory`] / [`AudioSink`] backed by cpal (CoreAudio on macOS). Feeds
//! the engine's interleaved `i16` frames to the default output device through a
//! bounded ring so `write` paces decode to real-time. Enabled by the
//! `cpal-sink` feature; the Pi appliance links its own ALSA sink instead.

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use anyhow::{anyhow, Context, Result};
use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use crossbeam_channel::{bounded, Receiver, Sender};

use crate::sink::{AudioSink, SinkFactory};

/// Opens [`CpalSink`]s on the host's default output device.
pub struct CpalSinkFactory;

impl SinkFactory for CpalSinkFactory {
    fn open(&self, rate: u32, channels: usize) -> Result<Box<dyn AudioSink>> {
        Ok(Box::new(CpalSink::open(rate, channels)?))
    }
}

/// Drains chunks from `rx` into the cpal output buffer, carrying leftovers
/// between callback invocations. `pending` tracks samples still to be played so
/// the sink can block on `drain()` until the device has actually finished.
struct Callback {
    rx: Receiver<Vec<i16>>,
    left: Vec<i16>,
    pos: usize,
    pending: Arc<AtomicUsize>,
}

impl Callback {
    fn next_sample(&mut self) -> Option<i16> {
        loop {
            if self.pos < self.left.len() {
                let s = self.left[self.pos];
                self.pos += 1;
                self.pending.fetch_sub(1, Ordering::Relaxed);
                return Some(s);
            }
            match self.rx.try_recv() {
                Ok(chunk) => {
                    self.left = chunk;
                    self.pos = 0;
                }
                Err(_) => return None,
            }
        }
    }
}

pub struct CpalSink {
    _stream: cpal::Stream,
    tx: Sender<Vec<i16>>,
    pending: Arc<AtomicUsize>,
}

impl CpalSink {
    pub fn open(rate: u32, channels: usize) -> Result<Self> {
        let host = cpal::default_host();
        let device = host
            .default_output_device()
            .ok_or_else(|| anyhow!("no default output device"))?;
        let config = cpal::StreamConfig {
            channels: channels.max(1) as u16,
            sample_rate: cpal::SampleRate(rate),
            buffer_size: cpal::BufferSize::Default,
        };
        let sample_format = device
            .default_output_config()
            .context("default output config")?
            .sample_format();

        // ~2 s of jitter room; `tx.send` blocks past that → paces decode.
        let (tx, rx) = bounded::<Vec<i16>>(64);
        let pending = Arc::new(AtomicUsize::new(0));
        let cb = Arc::new(Mutex::new(Callback {
            rx,
            left: Vec::new(),
            pos: 0,
            pending: pending.clone(),
        }));
        let err_fn = |e| tracing::warn!("cpal stream error: {e}");

        let stream = match sample_format {
            cpal::SampleFormat::F32 => {
                let cb = cb.clone();
                device.build_output_stream(
                    &config,
                    move |out: &mut [f32], _| {
                        let mut cb = cb.lock().unwrap();
                        for o in out.iter_mut() {
                            *o = cb.next_sample().map(|s| s as f32 / 32768.0).unwrap_or(0.0);
                        }
                    },
                    err_fn,
                    None,
                )?
            }
            cpal::SampleFormat::I16 => {
                let cb = cb.clone();
                device.build_output_stream(
                    &config,
                    move |out: &mut [i16], _| {
                        let mut cb = cb.lock().unwrap();
                        for o in out.iter_mut() {
                            *o = cb.next_sample().unwrap_or(0);
                        }
                    },
                    err_fn,
                    None,
                )?
            }
            other => return Err(anyhow!("unsupported output sample format: {other:?}")),
        };
        stream.play().context("start cpal stream")?;
        Ok(CpalSink {
            _stream: stream,
            tx,
            pending,
        })
    }
}

impl AudioSink for CpalSink {
    fn write(&mut self, interleaved: &[i16]) -> Result<()> {
        self.pending.fetch_add(interleaved.len(), Ordering::Relaxed);
        self.tx
            .send(interleaved.to_vec())
            .map_err(|_| anyhow!("cpal sink closed"))
    }

    fn drain(&mut self) {
        // Wait until the callback has consumed every queued sample.
        while self.pending.load(Ordering::Relaxed) > 0 {
            std::thread::sleep(std::time::Duration::from_millis(20));
        }
        // A little tail so the device flushes its own buffer.
        std::thread::sleep(std::time::Duration::from_millis(120));
    }
}
