//! Single-file audiobook playback: decode (symphonia) → optional resume seek →
//! dynamics → [`AudioSink`], writing a position file for resume / auto-advance.
//!
//! Extracted from the Pi appliance's `blazend-player` (minus the radio/HLS
//! streaming path and the ALSA output, which becomes the platform sink). Like
//! that player this plays ONE file; chapter auto-advance is the orchestrator's
//! job (the Python agent loops chapters), matching the appliance exactly.

use std::collections::VecDeque;
use std::fs::File;
use std::path::{Path, PathBuf};
use std::thread;

use anyhow::{anyhow, Context, Result};
use crossbeam_channel::{bounded, Receiver, Sender, TryRecvError};
use symphonia::core::audio::SampleBuffer;
use symphonia::core::codecs::{Decoder, DecoderOptions, CODEC_TYPE_NULL};
use symphonia::core::errors::Error as SymError;
use symphonia::core::formats::{FormatOptions, FormatReader, SeekMode, SeekTo};
use symphonia::core::io::{MediaSource, MediaSourceStream};
use symphonia::core::meta::MetadataOptions;
use symphonia::core::probe::Hint;
use symphonia::core::units::Time;

use crate::dynamics::{Dynamics, DynamicsConfig};
use crate::http_source::HttpMediaSource;
use crate::sink::SinkFactory;

const CHANNEL_CHUNKS: usize = 32;

/// What to play and how.
pub struct FileConfig {
    pub source: PathBuf,
    pub start_seconds: f64,
    pub position_file: Option<PathBuf>,
    pub prebuffer_ms: u32,
    pub dynamics: DynamicsConfig,
}

impl FileConfig {
    pub fn new(source: impl Into<PathBuf>) -> Self {
        FileConfig {
            source: source.into(),
            start_seconds: 0.0,
            position_file: None,
            prebuffer_ms: 200,
            dynamics: DynamicsConfig::defaults(),
        }
    }
}

fn open_media(source: &Path) -> Result<(MediaSourceStream, Hint)> {
    let raw = source.to_string_lossy();
    let (media, ext): (Box<dyn MediaSource>, Option<String>) =
        if raw.starts_with("http://") || raw.starts_with("https://") {
            // Stream a chapter straight off a mesh media peer (no local re-render).
            let http = HttpMediaSource::open(&raw).with_context(|| format!("open {raw}"))?;
            (Box::new(http), url_extension(&raw))
        } else {
            let file = File::open(source).with_context(|| format!("open {}", source.display()))?;
            (
                Box::new(file),
                source
                    .extension()
                    .and_then(|e| e.to_str())
                    .map(String::from),
            )
        };
    let mss = MediaSourceStream::new(media, Default::default());
    let mut hint = Hint::new();
    if let Some(ext) = ext {
        hint.with_extension(&ext);
    }
    Ok((mss, hint))
}

/// The file extension of a URL path (`…/001.mp3?x=1` → `mp3`), for the probe hint.
fn url_extension(url: &str) -> Option<String> {
    let path = url.split(['?', '#']).next().unwrap_or(url);
    let name = path.rsplit('/').next().unwrap_or(path);
    name.rsplit_once('.').map(|(_, ext)| ext.to_string())
}

type DecoderBundle = (
    Box<dyn FormatReader>,
    Box<dyn Decoder>,
    u32,
    Option<u32>,
    Option<usize>,
);

fn make_decoder(mss: MediaSourceStream, hint: &Hint) -> Result<DecoderBundle> {
    let probed = symphonia::default::get_probe()
        .format(
            hint,
            mss,
            &FormatOptions {
                enable_gapless: true,
                ..Default::default()
            },
            &MetadataOptions::default(),
        )
        .context("probe/format")?;
    let format = probed.format;
    let track = format
        .tracks()
        .iter()
        .find(|t| t.codec_params.codec != CODEC_TYPE_NULL)
        .ok_or_else(|| anyhow!("no decodable audio track"))?;
    let track_id = track.id;
    let rate = track.codec_params.sample_rate;
    let channels = track.codec_params.channels.map(|c| c.count());
    let decoder = symphonia::default::get_codecs()
        .make(&track.codec_params, &DecoderOptions::default())
        .context("make decoder")?;
    Ok((format, decoder, track_id, rate, channels))
}

fn prime_decode(
    format: &mut dyn FormatReader,
    decoder: &mut dyn Decoder,
    track_id: u32,
) -> Result<(u32, usize, Vec<i16>)> {
    loop {
        let packet = format.next_packet().context("read first packet")?;
        if packet.track_id() != track_id {
            continue;
        }
        match decoder.decode(&packet) {
            Ok(audio) => {
                let spec = *audio.spec();
                let mut sb = SampleBuffer::<i16>::new(audio.capacity() as u64, spec);
                sb.copy_interleaved_ref(audio);
                return Ok((spec.rate, spec.channels.count(), sb.samples().to_vec()));
            }
            Err(SymError::DecodeError(_)) => continue,
            Err(e) => return Err(e.into()),
        }
    }
}

fn decode_loop(
    mut format: Box<dyn FormatReader>,
    mut decoder: Box<dyn Decoder>,
    track_id: u32,
    prime: Option<Vec<i16>>,
    tx: Sender<Vec<i16>>,
) {
    if let Some(p) = prime {
        if tx.send(p).is_err() {
            return;
        }
    }
    let mut sb: Option<SampleBuffer<i16>> = None;
    loop {
        let packet = match format.next_packet() {
            Ok(p) => p,
            Err(SymError::IoError(e)) if e.kind() == std::io::ErrorKind::UnexpectedEof => break,
            Err(SymError::ResetRequired) => break,
            Err(_) => break,
        };
        if packet.track_id() != track_id {
            continue;
        }
        match decoder.decode(&packet) {
            Ok(audio) => {
                if sb.is_none() {
                    let spec = *audio.spec();
                    sb = Some(SampleBuffer::new(audio.capacity() as u64, spec));
                }
                let b = sb.as_mut().unwrap();
                b.copy_interleaved_ref(audio);
                if tx.send(b.samples().to_vec()).is_err() {
                    break;
                }
            }
            Err(SymError::DecodeError(_)) => {}
            Err(SymError::IoError(e)) if e.kind() == std::io::ErrorKind::UnexpectedEof => break,
            Err(_) => break,
        }
    }
}

/// Atomically write the current playback position (seconds + whether the file
/// reached its natural end) so the orchestrator can resume / auto-advance.
/// Same JSON shape as the Pi appliance's `blazend-player`.
pub fn write_position(path: &Path, seconds: f64, done: bool) {
    let tmp = path.with_extension("tmp");
    if std::fs::write(
        &tmp,
        format!("{{\"seconds\":{seconds:.1},\"done\":{done}}}"),
    )
    .is_ok()
    {
        let _ = std::fs::rename(&tmp, path);
    }
}

/// Play `cfg.source` once through a sink from `factory`. Returns after the file
/// ends naturally (or the source errors); writes the position file throughout.
pub fn play_file(factory: &dyn SinkFactory, cfg: &FileConfig) -> Result<()> {
    let (mss, hint) = open_media(&cfg.source)?;
    let (mut format, mut decoder, track_id, rate_opt, ch_opt) = make_decoder(mss, &hint)?;

    // Resume: seek into the file before decoding (best-effort).
    if cfg.start_seconds > 0.0 {
        let to = SeekTo::Time {
            time: Time::from(cfg.start_seconds),
            track_id: Some(track_id),
        };
        if let Err(e) = format.seek(SeekMode::Coarse, to) {
            tracing::warn!(
                "seek to {}s failed ({e}); starting from the top",
                cfg.start_seconds
            );
        }
    }

    let (rate, channels, prime) = match (rate_opt, ch_opt) {
        (Some(r), Some(c)) if r > 0 && c > 0 => (r, c, None),
        _ => {
            let (r, c, s) = prime_decode(&mut *format, &mut *decoder, track_id)?;
            (r, c, Some(s))
        }
    };
    let channels = channels.max(1);

    let (tx, rx) = bounded::<Vec<i16>>(CHANNEL_CHUNKS);
    let decoder_thread = thread::Builder::new()
        .name("decode".into())
        .spawn(move || decode_loop(format, decoder, track_id, prime, tx))?;

    let prebuffer_frames = (rate as usize * cfg.prebuffer_ms as usize / 1000).max(1);
    let mut sink = factory.open(rate, channels)?;
    run_output(
        sink.as_mut(),
        rate,
        channels,
        prebuffer_frames,
        cfg.dynamics.resolve(),
        cfg.start_seconds,
        cfg.position_file.as_deref(),
        rx,
    );
    let _ = decoder_thread.join();
    Ok(())
}

#[allow(clippy::too_many_arguments)]
fn run_output(
    sink: &mut dyn crate::sink::AudioSink,
    rate: u32,
    channels: usize,
    prebuffer_frames: usize,
    cfg: crate::dynamics::DynamicsCfg,
    start_seconds: f64,
    position_file: Option<&Path>,
    rx: Receiver<Vec<i16>>,
) {
    let mut dsp = Dynamics::new(cfg, rate, channels.max(1));
    let frame = channels.max(1);
    let period_frames = (rate as usize / 10).max(256); // ~100 ms write chunks
    let hi_watermark = prebuffer_frames * frame * 4;

    let mut buf: VecDeque<i16> = VecDeque::new();
    let mut eof = false;

    while buf.len() < prebuffer_frames * frame {
        match rx.recv() {
            Ok(mut c) => {
                dsp.process(&mut c);
                buf.extend(c);
            }
            Err(_) => {
                eof = true;
                break;
            }
        }
    }

    let mut frames_played = 0u64;
    let mut next_report_frames = 0u64;
    let mut next_meter_frames = 5 * u64::from(rate.max(1));
    loop {
        if let Some(pf) = position_file {
            if frames_played >= next_report_frames {
                let secs = start_seconds + frames_played as f64 / f64::from(rate.max(1));
                write_position(pf, secs, false);
                next_report_frames = frames_played + 2 * u64::from(rate.max(1));
            }
        }
        if frames_played >= next_meter_frames {
            tracing::info!(
                out_dbfs = dsp.meter_dbfs(),
                level_gain_db = dsp.gain_db(),
                "level"
            );
            next_meter_frames = frames_played + 5 * u64::from(rate.max(1));
        }
        if !eof {
            loop {
                match rx.try_recv() {
                    Ok(mut c) => {
                        dsp.process(&mut c);
                        buf.extend(c);
                        if buf.len() >= hi_watermark {
                            break;
                        }
                    }
                    Err(TryRecvError::Empty) => break,
                    Err(TryRecvError::Disconnected) => {
                        eof = true;
                        break;
                    }
                }
            }
        }
        if buf.len() < frame {
            if eof {
                break;
            }
            match rx.recv() {
                Ok(mut c) => {
                    dsp.process(&mut c);
                    buf.extend(c);
                }
                Err(_) => eof = true,
            }
            continue;
        }
        let n = (buf.len() / frame).min(period_frames);
        let chunk: Vec<i16> = buf.drain(..n * frame).collect();
        if sink.write(&chunk).is_err() {
            break;
        }
        frames_played += n as u64;
    }
    sink.drain();
    if let Some(pf) = position_file {
        let final_secs = start_seconds + frames_played as f64 / f64::from(rate.max(1));
        write_position(pf, final_secs, true);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::sink::{AudioSink, CountingSink};
    use std::sync::{Arc, Mutex};

    struct SharedSink(Arc<Mutex<CountingSink>>);
    impl AudioSink for SharedSink {
        fn write(&mut self, b: &[i16]) -> Result<()> {
            self.0.lock().unwrap().write(b)
        }
        fn drain(&mut self) {
            self.0.lock().unwrap().drain();
        }
    }
    struct SharedFactory(Arc<Mutex<CountingSink>>);
    impl SinkFactory for SharedFactory {
        fn open(&self, _rate: u32, _ch: usize) -> Result<Box<dyn AudioSink>> {
            Ok(Box::new(SharedSink(self.0.clone())))
        }
    }

    fn write_wav(path: &Path, secs: f32, rate: u32) {
        let spec = hound::WavSpec {
            channels: 1,
            sample_rate: rate,
            bits_per_sample: 16,
            sample_format: hound::SampleFormat::Int,
        };
        let mut w = hound::WavWriter::create(path, spec).unwrap();
        let n = (secs * rate as f32) as usize;
        for i in 0..n {
            let t = i as f32 / rate as f32;
            let s = (2.0 * std::f32::consts::PI * 220.0 * t).sin() * 8000.0;
            w.write_sample(s as i16).unwrap();
        }
        w.finalize().unwrap();
    }

    #[test]
    fn plays_a_file_and_writes_done_position() {
        let dir = tempfile::tempdir().unwrap();
        let wav = dir.path().join("ch.wav");
        write_wav(&wav, 0.5, 48_000);
        let pos = dir.path().join("pos.json");
        let sink = Arc::new(Mutex::new(CountingSink::default()));
        let cfg = FileConfig {
            source: wav,
            start_seconds: 0.0,
            position_file: Some(pos.clone()),
            prebuffer_ms: 50,
            dynamics: DynamicsConfig::off(),
        };
        play_file(&SharedFactory(sink.clone()), &cfg).unwrap();
        let s = sink.lock().unwrap();
        assert!(
            s.frames_written >= 48_000 / 2,
            "wrote {} frames",
            s.frames_written
        );
        assert!(s.drained);
        let written = std::fs::read_to_string(&pos).unwrap();
        assert!(written.contains("\"done\":true"), "position: {written}");
    }

    #[test]
    fn start_seconds_skips_frames() {
        let dir = tempfile::tempdir().unwrap();
        let wav = dir.path().join("ch.wav");
        write_wav(&wav, 1.0, 48_000);
        let full = Arc::new(Mutex::new(CountingSink::default()));
        let half = Arc::new(Mutex::new(CountingSink::default()));
        play_file(&SharedFactory(full.clone()), &FileConfig::new(wav.clone())).unwrap();
        let mut cfg = FileConfig::new(wav);
        cfg.start_seconds = 0.5;
        play_file(&SharedFactory(half.clone()), &cfg).unwrap();
        let f = full.lock().unwrap().frames_written;
        let h = half.lock().unwrap().frames_written;
        assert!(
            h < f,
            "seeked play ({h}) should write fewer frames than full ({f})"
        );
    }
}
