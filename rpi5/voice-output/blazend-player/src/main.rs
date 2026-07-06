//! `blazend-player` — buffered stream/file audio player.
//!
//! Plays an internet-radio stream (`http(s)://…`) or a local recording (any
//! path) to an ALSA device, decoding with pure-Rust **symphonia** (mp3 / aac /
//! flac / ogg-vorbis / wav). The lag fix versus the old `ffmpeg -f alsa` path:
//! a **prebuffered jitter buffer** between a decode thread and the ALSA write
//! loop, plus an explicitly-sized ALSA buffer — so network/scheduler jitter is
//! absorbed instead of underrunning the device.
//!
//! Pipeline: source (`ureq` stream / `File`) → symphonia decode → interleaved
//! `i16` chunks → bounded `crossbeam` channel (backpressure) → ALSA `writei`,
//! paced by blocking writes. Runs until SIGTERM (drop-in for the ffmpeg
//! subprocess); local files exit on EOF unless `--loop`.

use std::collections::VecDeque;
use std::fs::File;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::path::Path;
use std::thread;
use std::time::Duration;

use anyhow::{anyhow, Context, Result};
use clap::Parser;
use crossbeam_channel::{bounded, Receiver, Sender, TryRecvError};
use symphonia::core::audio::SampleBuffer;
use symphonia::core::codecs::{Decoder, DecoderOptions, CODEC_TYPE_NULL};
use symphonia::core::errors::Error as SymError;
use symphonia::core::formats::{FormatOptions, FormatReader, SeekMode, SeekTo};
use symphonia::core::io::{MediaSourceStream, ReadOnlySource};
use symphonia::core::meta::MetadataOptions;
use symphonia::core::probe::Hint;
use symphonia::core::units::Time;

/// Max decoded chunks queued between the decode thread and the ALSA writer.
/// Generous headroom; the producer blocks on a full channel (backpressure).
const CHANNEL_CHUNKS: usize = 512;

#[derive(Parser, Debug)]
#[command(name = "blazend-player", version)]
struct Args {
    /// `http(s)://…` live stream, or a local file path.
    #[arg(long)]
    source: String,
    /// ALSA output PCM (full name). The appliance ships a single Jabra SPEAK
    /// 410 USB as the only audio device, so that is the default.
    #[arg(long, default_value = "plughw:CARD=USB,DEV=0")]
    device: String,
    /// Prebuffer filled before playback starts (ms) — smooths startup + jitter.
    #[arg(long, default_value_t = 1500)]
    prebuffer_ms: u32,
    /// Jitter-buffer target depth held during playback (ms).
    #[arg(long, default_value_t = 4000)]
    buffer_ms: u32,
    /// ALSA hardware buffer size (ms).
    #[arg(long, default_value_t = 500)]
    alsa_buffer_ms: u32,
    /// Linear gain applied to every sample.
    #[arg(long, default_value_t = 1.0)]
    gain: f32,
    /// Loop a local file on EOF (ignored for streams).
    #[arg(long, default_value_t = false)]
    r#loop: bool,
    /// Seek this many seconds into a local file before playing (audiobook resume).
    #[arg(long, default_value_t = 0.0)]
    start_seconds: f64,
    /// If set, write the current playback position (JSON) here every ~2 s, so the
    /// orchestrator can remember where an audiobook was stopped.
    #[arg(long)]
    position_file: Option<String>,
    /// Disable the always-on loudness leveler (AGC). By default the player measures
    /// its own output level and auto-holds a constant loudness for every source, so
    /// a quiet audiobook comes up to the same level as radio.
    #[arg(long = "no-level", default_value_t = false)]
    no_level: bool,
    /// Leveler target loudness (dBFS RMS) — quiet content is boosted toward this.
    #[arg(long, default_value_t = -16.0)]
    target_db: f32,
    /// Max boost the leveler may apply to very quiet content (dB).
    #[arg(long, default_value_t = 20.0)]
    max_boost_db: f32,
    /// Brick-wall limiter ceiling (dBFS) — leveling/compression never clip past this.
    #[arg(long, default_value_t = -1.0)]
    limit_db: f32,
    /// Enable the speech compressor (books/podcasts) for intelligibility. OFF for
    /// music/radio, which keep their dynamics.
    #[arg(long, default_value_t = false)]
    compress: bool,
    /// Speech compressor threshold (dBFS).
    #[arg(long, default_value_t = -24.0)]
    comp_threshold_db: f32,
    /// Speech compressor ratio (N:1).
    #[arg(long, default_value_t = 3.0)]
    comp_ratio: f32,
    /// Speech compressor makeup gain (dB).
    #[arg(long, default_value_t = 3.0)]
    comp_makeup_db: f32,
}

/// `http(s)` source → live stream; anything else → local file.
fn is_url(s: &str) -> bool {
    let l = s.trim_start().to_ascii_lowercase();
    l.starts_with("http://") || l.starts_with("https://")
}

/// Map an HTTP `Content-Type` to a symphonia format hint extension.
fn hint_for_content_type(ct: &str) -> Option<&'static str> {
    let ct = ct
        .split(';')
        .next()
        .unwrap_or("")
        .trim()
        .to_ascii_lowercase();
    match ct.as_str() {
        "audio/mpeg" | "audio/mp3" => Some("mp3"),
        "audio/aac" | "audio/aacp" | "audio/x-aac" => Some("aac"),
        "audio/ogg" | "application/ogg" | "audio/vorbis" => Some("ogg"),
        "audio/flac" | "audio/x-flac" => Some("flac"),
        "audio/wav" | "audio/x-wav" | "audio/wave" => Some("wav"),
        _ => None,
    }
}

/// Apply a linear gain with hard clamping (no-op at unity).
fn apply_gain(samples: &mut [i16], gain: f32) {
    if (gain - 1.0).abs() < f32::EPSILON {
        return;
    }
    for s in samples.iter_mut() {
        *s = (*s as f32 * gain).clamp(i16::MIN as f32, i16::MAX as f32) as i16;
    }
}

/// Decibels (full-scale) → linear amplitude ratio.
fn db_to_lin(db: f32) -> f32 {
    10f32.powf(db / 20.0)
}

const I16_SCALE: f32 = 32768.0;

/// Static config for the output dynamics chain, resolved from CLI flags once.
#[derive(Clone, Copy, Debug)]
struct DynamicsCfg {
    pre_gain: f32,   // manual --gain, applied first
    level: bool,     // always-on loudness leveler (AGC)
    target_rms: f32, // linear target (from --target-db)
    max_boost: f32,  // linear ceiling on AGC gain (from --max-boost-db)
    max_cut: f32,    // linear floor on AGC gain (leveler may also tame hot sources)
    compress: bool,  // speech compressor (books/podcasts)
    comp_threshold: f32,
    comp_ratio: f32,
    comp_makeup: f32,
    limit_ceil: f32, // linear brick-wall ceiling (from --limit-db)
}

impl DynamicsCfg {
    fn from_args(a: &Args) -> Self {
        DynamicsCfg {
            pre_gain: a.gain,
            level: !a.no_level,
            target_rms: db_to_lin(a.target_db),
            max_boost: db_to_lin(a.max_boost_db),
            max_cut: db_to_lin(-12.0), // never attenuate a hot source by more than 12 dB
            compress: a.compress,
            comp_threshold: db_to_lin(a.comp_threshold_db),
            comp_ratio: a.comp_ratio.max(1.0),
            comp_makeup: db_to_lin(a.comp_makeup_db),
            limit_ceil: db_to_lin(a.limit_db),
        }
    }

    /// Nothing to do but the manual gain → fall back to the plain `apply_gain`.
    fn is_noop(&self) -> bool {
        !self.level && !self.compress
    }
}

/// Output-side dynamics: an always-on loudness **leveler** (measures the player's
/// own output and slews a gain toward a target RMS, so a quiet audiobook rises to
/// the same loudness as radio), an optional speech **compressor** (intelligibility
/// for books/podcasts — never music), and a brick-wall **limiter**. Operates in
/// place on interleaved `i16` chunks, holding envelope + gain state across chunks.
struct Dynamics {
    cfg: DynamicsCfg,
    a_rms: f32,      // loudness-estimate EMA coefficient (~400 ms)
    a_up: f32,       // leveler gain rise — slow (boosting quiet, avoids pumping)
    a_down: f32,     // leveler gain fall — fast (taming loud)
    a_att: f32,      // compressor attack (~5 ms)
    a_rel: f32,      // compressor release (~120 ms)
    gate: f32,       // below this RMS = silence → hold the leveler gain
    rms_sq: f32,     // smoothed mean-square of the pre-level signal
    level_gain: f32, // current smoothed leveler gain (linear)
    desired_gain: f32,
    refresh: u32,    // samples until the desired gain is recomputed
    comp_env: f32,   // compressor envelope follower
    meter_peak: f32, // output peak since the last drain (for logging)
}

impl Dynamics {
    fn new(cfg: DynamicsCfg, rate: u32, channels: usize) -> Self {
        let fs = (rate.max(1) as f32) * (channels.max(1) as f32);
        let coef = |tau: f32| 1.0 - (-1.0 / (tau * fs)).exp();
        Dynamics {
            cfg,
            a_rms: coef(0.4),
            a_up: coef(1.0),
            a_down: coef(0.15),
            a_att: coef(0.005),
            a_rel: coef(0.120),
            gate: db_to_lin(-50.0),
            rms_sq: cfg.target_rms * cfg.target_rms, // start at target → no startup pump
            level_gain: 1.0,
            desired_gain: 1.0,
            refresh: 0,
            comp_env: 0.0,
            meter_peak: 0.0,
        }
    }

    /// Process one interleaved chunk in place: pre-gain → leveler → compressor →
    /// limiter. State carries across calls so the loudness stays constant.
    fn process(&mut self, samples: &mut [i16]) {
        if self.cfg.is_noop() {
            apply_gain(samples, self.cfg.pre_gain);
            for s in samples.iter() {
                let a = (*s as f32 / I16_SCALE).abs();
                self.meter_peak = self.meter_peak.max(a);
            }
            return;
        }
        for s in samples.iter_mut() {
            let mut x = (*s as f32 / I16_SCALE) * self.cfg.pre_gain;
            if self.cfg.level {
                self.rms_sq += self.a_rms * (x * x - self.rms_sq);
                if self.refresh == 0 {
                    let rms = self.rms_sq.sqrt();
                    self.desired_gain = if rms > self.gate {
                        (self.cfg.target_rms / rms).clamp(self.cfg.max_cut, self.cfg.max_boost)
                    } else {
                        self.level_gain // silence: hold, don't pump up the noise floor
                    };
                    self.refresh = 64;
                }
                self.refresh -= 1;
                let a = if self.desired_gain < self.level_gain {
                    self.a_down
                } else {
                    self.a_up
                };
                self.level_gain += a * (self.desired_gain - self.level_gain);
                x *= self.level_gain;
            }
            if self.cfg.compress {
                let mag = x.abs();
                let a = if mag > self.comp_env {
                    self.a_att
                } else {
                    self.a_rel
                };
                self.comp_env += a * (mag - self.comp_env);
                if self.comp_env > self.cfg.comp_threshold {
                    let over = self.comp_env / self.cfg.comp_threshold;
                    x *= over.powf(1.0 / self.cfg.comp_ratio - 1.0); // downward gain
                }
                x *= self.cfg.comp_makeup;
            }
            x = x.clamp(-self.cfg.limit_ceil, self.cfg.limit_ceil);
            self.meter_peak = self.meter_peak.max(x.abs());
            *s = (x * I16_SCALE).clamp(i16::MIN as f32, i16::MAX as f32) as i16;
        }
    }

    /// Output peak since the last call, in dBFS (drains the meter).
    fn meter_dbfs(&mut self) -> f32 {
        let p = self.meter_peak.max(1e-6);
        self.meter_peak = 0.0;
        20.0 * p.log10()
    }

    /// Current leveler gain in dB (0 dB = unity).
    fn gain_db(&self) -> f32 {
        20.0 * self.level_gain.max(1e-6).log10()
    }
}

type StreamReader = Box<dyn Read + Send + Sync>;

/// Open an `http://` stream with a hand-rolled request so we tolerate
/// Shoutcast's non-HTTP `ICY 200 OK` status line (which `ureq` rejects). The
/// returned reader is positioned at the start of the audio body.
fn open_http_icy(source: &str) -> Result<(StreamReader, Option<String>)> {
    let rest = source.trim().strip_prefix("http://").unwrap_or(source);
    let (hostport, path) = match rest.find('/') {
        Some(i) => (&rest[..i], &rest[i..]),
        None => (rest, "/"),
    };
    let (host, port) = match hostport.rsplit_once(':') {
        Some((h, p)) => (h.to_string(), p.parse::<u16>().unwrap_or(80)),
        None => (hostport.to_string(), 80),
    };
    let stream = TcpStream::connect((host.as_str(), port))
        .with_context(|| format!("connect {host}:{port}"))?;
    stream.set_read_timeout(Some(Duration::from_secs(20)))?;
    let mut w = stream.try_clone()?;
    write!(
        w,
        "GET {path} HTTP/1.0\r\nHost: {host}\r\nUser-Agent: blazend-player\r\n\
         Icy-MetaData: 0\r\nAccept: */*\r\nConnection: close\r\n\r\n"
    )?;
    w.flush()?;

    let mut reader = BufReader::new(stream);
    let mut status = String::new();
    reader.read_line(&mut status)?; // "ICY 200 OK" or "HTTP/1.x 200 OK"
    if !status.contains("200") {
        return Err(anyhow!("stream returned: {}", status.trim()));
    }
    let mut content_type = None;
    loop {
        let mut line = String::new();
        if reader.read_line(&mut line)? == 0 {
            break;
        }
        let line = line.trim_end();
        if line.is_empty() {
            break; // end of headers; body follows in the BufReader
        }
        if let Some((k, v)) = line.split_once(':') {
            if k.trim().eq_ignore_ascii_case("content-type") {
                content_type = Some(v.trim().to_string());
            }
        }
    }
    tracing::info!(status = %status.trim(), "stream connected (raw http/icy)");
    Ok((Box::new(reader), content_type))
}

/// A ureq agent with a native-tls (OpenSSL) connector. ureq's `native-tls`
/// feature provides support but does NOT auto-configure the default agent, so it
/// must be built explicitly — otherwise HTTPS fails with "no TLS backend
/// configured". OpenSSL also accepts servers rustls rejects (nadaje.com / Radio
/// Kraków fail the rustls handshake).
fn https_agent() -> ureq::Agent {
    match native_tls::TlsConnector::new() {
        Ok(c) => ureq::AgentBuilder::new()
            .tls_connector(std::sync::Arc::new(c))
            .build(),
        Err(e) => {
            tracing::warn!("native-tls init failed ({e}); using default agent");
            ureq::agent()
        }
    }
}

/// A `.m3u8` URL → HLS. Reliable path for capacity-capped Shoutcast stations
/// (e.g. Trójka) whose CDN HLS has no listener cap.
fn is_hls(s: &str) -> bool {
    s.trim()
        .split('?')
        .next()
        .unwrap_or("")
        .to_ascii_lowercase()
        .ends_with(".m3u8")
}

/// Resolve a possibly-relative playlist/segment reference against a playlist URL.
fn resolve_url(base: &str, r: &str) -> String {
    let r = r.trim();
    if r.starts_with("http://") || r.starts_with("https://") {
        return r.to_string();
    }
    match base.rfind('/') {
        Some(i) => format!("{}{}", &base[..=i], r),
        None => r.to_string(),
    }
}

/// Length of a leading ID3v2 tag (Polskie Radio's `.aac` segments carry one), so
/// the bytes handed to symphonia are clean ADTS-AAC.
fn id3_len(buf: &[u8]) -> usize {
    if buf.len() >= 10 && &buf[0..3] == b"ID3" {
        let sz = ((buf[6] as usize & 0x7f) << 21)
            | ((buf[7] as usize & 0x7f) << 14)
            | ((buf[8] as usize & 0x7f) << 7)
            | (buf[9] as usize & 0x7f);
        (10 + sz).min(buf.len())
    } else {
        0
    }
}

/// Live-HLS reader: polls a media (chunk) playlist and serves its `.aac` segments
/// as one continuous ADTS-AAC byte stream for symphonia. Fetches block the decode
/// thread — the ~10 s segments keep the jitter buffer well ahead.
struct HlsReader {
    agent: ureq::Agent,
    origin: String, // the URL we were given (master or media playlist)
    media: String,  // resolved media (chunk) playlist URL
    next_seq: u64,  // next EXT-X-MEDIA-SEQUENCE to play
    started: bool,
    buf: Vec<u8>,
    pos: usize,
}

impl HlsReader {
    fn new(url: &str) -> Result<Self> {
        let agent = https_agent();
        let media = Self::resolve_media(&agent, url)?;
        Ok(Self {
            agent,
            origin: url.to_string(),
            media,
            next_seq: 0,
            started: false,
            buf: Vec::new(),
            pos: 0,
        })
    }

    fn get_text(agent: &ureq::Agent, url: &str) -> Result<String> {
        Ok(agent
            .get(url)
            .set("User-Agent", "blazend-player")
            .call()?
            .into_string()?)
    }

    /// A master playlist (has EXT-X-STREAM-INF) → its first variant; else the URL
    /// is already a media playlist.
    fn resolve_media(agent: &ureq::Agent, url: &str) -> Result<String> {
        let text = Self::get_text(agent, url)?;
        if text.contains("#EXT-X-STREAM-INF") {
            for line in text.lines() {
                let l = line.trim();
                if !l.is_empty() && !l.starts_with('#') {
                    return Ok(resolve_url(url, l));
                }
            }
        }
        Ok(url.to_string())
    }

    /// Download the next unplayed segment into `buf` (polls for new segments; the
    /// per-request chunklist token can expire → re-resolve from the origin).
    fn load_next(&mut self) -> Result<()> {
        for _ in 0..60 {
            let text = match Self::get_text(&self.agent, &self.media) {
                Ok(t) => t,
                Err(_) => {
                    self.media = Self::resolve_media(&self.agent, &self.origin)?;
                    Self::get_text(&self.agent, &self.media)?
                }
            };
            let mut base_seq = 0u64;
            let mut segs: Vec<String> = Vec::new();
            for line in text.lines() {
                let l = line.trim();
                if let Some(v) = l.strip_prefix("#EXT-X-MEDIA-SEQUENCE:") {
                    base_seq = v.trim().parse().unwrap_or(0);
                } else if !l.is_empty() && !l.starts_with('#') {
                    segs.push(l.to_string());
                }
            }
            if segs.is_empty() {
                thread::sleep(Duration::from_secs(1));
                continue;
            }
            let last = base_seq + segs.len() as u64 - 1;
            if !self.started {
                self.next_seq = last; // start at the live edge for low latency
                self.started = true;
            } else if self.next_seq < base_seq {
                self.next_seq = base_seq; // fell behind (segments expired) → catch up
            }
            if self.next_seq > last {
                thread::sleep(Duration::from_secs(2)); // wait for a fresh segment
                continue;
            }
            let seg = &segs[(self.next_seq - base_seq) as usize];
            let url = resolve_url(&self.media, seg);
            let mut bytes = Vec::new();
            self.agent
                .get(&url)
                .set("User-Agent", "blazend-player")
                .call()?
                .into_reader()
                .read_to_end(&mut bytes)?;
            let skip = id3_len(&bytes);
            self.buf = bytes.split_off(skip);
            self.pos = 0;
            self.next_seq += 1;
            return Ok(());
        }
        Err(anyhow!("HLS: no playable segment after polling"))
    }
}

impl Read for HlsReader {
    fn read(&mut self, out: &mut [u8]) -> std::io::Result<usize> {
        while self.pos >= self.buf.len() {
            self.load_next()
                .map_err(|e| std::io::Error::other(e.to_string()))?;
        }
        let n = (self.buf.len() - self.pos).min(out.len());
        out[..n].copy_from_slice(&self.buf[self.pos..self.pos + n]);
        self.pos += n;
        Ok(n)
    }
}

/// Open the source as a symphonia media stream plus a format hint.
fn open_media(source: &str) -> Result<(MediaSourceStream, Hint)> {
    let mut hint = Hint::new();
    if is_url(source) {
        let (reader, ct): (StreamReader, Option<String>) = if is_hls(source) {
            // HLS: fetch + concatenate the .aac segments as one ADTS stream.
            tracing::info!(%source, "opening HLS stream (aac segments)");
            (
                Box::new(HlsReader::new(source)?),
                Some("audio/aac".to_string()),
            )
        } else if source.trim().to_ascii_lowercase().starts_with("https://") {
            // TLS path via native-tls/OpenSSL (assumes proper HTTP).
            let resp = https_agent()
                .get(source)
                .set("Icy-MetaData", "0")
                .set("User-Agent", "blazend-player")
                .call()
                .with_context(|| format!("HTTPS GET {source}"))?;
            let ct = resp.content_type().to_string();
            (resp.into_reader(), Some(ct))
        } else {
            open_http_icy(source)?
        };
        if let Some(ct) = &ct {
            if let Some(ext) = hint_for_content_type(ct) {
                hint.with_extension(ext);
            }
            hint.mime_type(ct);
        }
        let mss = MediaSourceStream::new(Box::new(ReadOnlySource::new(reader)), Default::default());
        tracing::info!(%source, content_type = ?ct, "opened stream");
        Ok((mss, hint))
    } else {
        let path = Path::new(source);
        if let Some(ext) = path.extension().and_then(|e| e.to_str()) {
            hint.with_extension(ext);
        }
        let file = File::open(path).with_context(|| format!("open file {source}"))?;
        let mss = MediaSourceStream::new(Box::new(file), Default::default());
        tracing::info!(%source, "opened file");
        Ok((mss, hint))
    }
}

/// Probe the container, pick the default audio track, and build a decoder.
/// Returns the format reader, decoder, track id, and (if known up front) the
/// sample rate + channel count.
#[allow(clippy::type_complexity)]
fn make_decoder(
    mss: MediaSourceStream,
    hint: &Hint,
) -> Result<(
    Box<dyn FormatReader>,
    Box<dyn Decoder>,
    u32,
    Option<u32>,
    Option<usize>,
)> {
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

/// Decode packets until the first audio buffer to learn rate/channels when the
/// container header didn't carry them. Returns the priming samples too.
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

/// Producer: decode the rest of the stream into interleaved i16 chunks.
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
            Err(SymError::IoError(e)) if e.kind() == std::io::ErrorKind::UnexpectedEof => {
                tracing::info!("source ended");
                break;
            }
            Err(SymError::ResetRequired) => {
                tracing::warn!("decoder reset required; stopping");
                break;
            }
            Err(e) => {
                tracing::warn!("read error: {e}");
                break;
            }
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
                    break; // consumer gone
                }
            }
            Err(SymError::DecodeError(e)) => tracing::debug!("decode error (skipping packet): {e}"),
            Err(SymError::IoError(e)) if e.kind() == std::io::ErrorKind::UnexpectedEof => break,
            Err(e) => {
                tracing::warn!("decode error: {e}");
                break;
            }
        }
    }
}

/// Consumer: prebuffer, then drain the jitter buffer to ALSA with paced
/// blocking writes. Underruns are recovered, never garbled.
#[allow(clippy::too_many_arguments)]
fn run_output(
    device: &str,
    rate: u32,
    channels: usize,
    prebuffer_frames: usize,
    alsa_buffer_ms: u32,
    cfg: DynamicsCfg,
    start_seconds: f64,
    position_file: Option<&str>,
    rx: Receiver<Vec<i16>>,
) -> Result<()> {
    use alsa::pcm::{Access, Format, HwParams, PCM};
    use alsa::{Direction, ValueOr};

    // Output dynamics: leveler (+ optional speech compressor) + limiter, measuring
    // the player's own output so every source holds a constant loudness.
    let mut dsp = Dynamics::new(cfg, rate, channels.max(1));

    let pcm = PCM::new(device, Direction::Playback, false)
        .with_context(|| format!("open ALSA device {device}"))?;
    {
        let hwp = HwParams::any(&pcm)?;
        hwp.set_channels(channels as u32)?;
        hwp.set_rate(rate, ValueOr::Nearest)?;
        hwp.set_format(Format::s16())?;
        hwp.set_access(Access::RWInterleaved)?;
        let buf_frames = (i64::from(rate) * i64::from(alsa_buffer_ms) / 1000).max(2048);
        hwp.set_buffer_size_near(buf_frames)?;
        hwp.set_period_size_near((buf_frames / 4).max(256), ValueOr::Nearest)?;
        pcm.hw_params(&hwp)?;
    }
    let io = pcm.io_i16()?;
    let frame = channels.max(1);
    let period_frames = (rate as usize / 10).max(256); // ~100 ms write chunks
    let hi_watermark = prebuffer_frames * frame * 4; // jitter-buffer ceiling

    let mut buf: VecDeque<i16> = VecDeque::new();
    let mut eof = false;

    // Prebuffer (blocking) before the first write.
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
    tracing::info!(
        rate,
        channels,
        prebuffer_ms = prebuffer_frames as u32 * 1000 / rate.max(1),
        "playback started"
    );

    let mut underruns = 0u64;
    let mut frames_played = 0u64;
    let mut next_report_frames = 0u64; // report position now, then every ~2 s
    let mut next_meter_frames = 5 * u64::from(rate.max(1)); // log the real level every ~5 s
    loop {
        if let Some(pf) = position_file {
            if frames_played >= next_report_frames {
                let secs = start_seconds + frames_played as f64 / f64::from(rate.max(1));
                write_position(pf, secs, false);
                next_report_frames = frames_played + 2 * u64::from(rate.max(1));
            }
        }
        if frames_played >= next_meter_frames {
            // "The real current sound volume" — output peak + the auto-leveler gain.
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
        match io.writei(&chunk) {
            Ok(_) => frames_played += n as u64,
            Err(e) => {
                underruns += 1;
                tracing::debug!("xrun: {e}");
                pcm.try_recover(e, true).ok();
                let _ = io.writei(&chunk);
            }
        }
    }
    let _ = pcm.drain();
    let final_secs = start_seconds + frames_played as f64 / f64::from(rate.max(1));
    if let Some(pf) = position_file {
        // Reaching here means the source ended naturally (the loop only breaks on
        // eof) → done=true so the orchestrator advances to the next chapter. A kill
        // mid-file never reaches this; the last periodic write (done=false) marks
        // the resume point.
        let _ = eof;
        write_position(pf, final_secs, true);
    }
    tracing::info!(
        underruns,
        seconds = frames_played / u64::from(rate.max(1)),
        "playback finished"
    );
    Ok(())
}

/// Atomically write the current playback position (seconds + whether the chapter
/// reached its natural end) so the orchestrator can resume / auto-advance.
fn write_position(path: &str, seconds: f64, done: bool) {
    let tmp = format!("{path}.tmp");
    if std::fs::write(
        &tmp,
        format!("{{\"seconds\":{seconds:.1},\"done\":{done}}}"),
    )
    .is_ok()
    {
        let _ = std::fs::rename(&tmp, path);
    }
}

/// Play the source once (a stream plays until killed / the server closes).
fn play_once(args: &Args) -> Result<()> {
    let (mss, hint) = open_media(&args.source)?;
    let (mut format, mut decoder, track_id, rate_opt, ch_opt) = make_decoder(mss, &hint)?;

    // Resume: seek into a local file before decoding (audiobook chapter resume).
    // Best-effort — an unseekable/short file just starts from the top.
    if args.start_seconds > 0.0 && !is_url(&args.source) {
        let to = SeekTo::Time {
            time: Time::from(args.start_seconds),
            track_id: Some(track_id),
        };
        match format.seek(SeekMode::Coarse, to) {
            Ok(seeked) => tracing::info!(
                requested = args.start_seconds,
                ts = seeked.actual_ts,
                "seeked"
            ),
            Err(e) => tracing::warn!(
                "seek to {}s failed ({e}); starting from the top",
                args.start_seconds
            ),
        }
    }

    let (rate, channels, prime) = match (rate_opt, ch_opt) {
        (Some(r), Some(c)) if r > 0 && c > 0 => (r, c, None),
        _ => {
            let (r, c, s) = prime_decode(&mut *format, &mut *decoder, track_id)?;
            (r, c, Some(s))
        }
    };

    let (tx, rx) = bounded::<Vec<i16>>(CHANNEL_CHUNKS);
    let decoder_thread = thread::Builder::new()
        .name("decode".into())
        .spawn(move || decode_loop(format, decoder, track_id, prime, tx))?;

    let prebuffer_frames = (rate as usize * args.prebuffer_ms as usize / 1000).max(1);
    run_output(
        &args.device,
        rate,
        channels.max(1),
        prebuffer_frames,
        args.alsa_buffer_ms,
        DynamicsCfg::from_args(args),
        args.start_seconds,
        args.position_file.as_deref(),
        rx,
    )?;
    let _ = decoder_thread.join();
    Ok(())
}

fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            // Quiet symphonia's benign mid-stream sync warnings (junk-skip /
            // main_data underflow on the first partial frame when joining live).
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "info,symphonia=error".into()),
        )
        .init();
    let args = Args::parse();
    let loop_file = args.r#loop && !is_url(&args.source);
    loop {
        if let Err(e) = play_once(&args) {
            tracing::error!("player error: {e:#}");
            std::process::exit(1);
        }
        if !loop_file {
            break;
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn url_detection() {
        assert!(is_url("http://stream"));
        assert!(is_url("HTTPS://stream"));
        assert!(!is_url("/tmp/rec.wav"));
        assert!(!is_url("song.mp3"));
    }

    #[test]
    fn content_type_hints() {
        assert_eq!(hint_for_content_type("audio/mpeg"), Some("mp3"));
        assert_eq!(
            hint_for_content_type("audio/aacp; charset=utf-8"),
            Some("aac")
        );
        assert_eq!(hint_for_content_type("application/ogg"), Some("ogg"));
        assert_eq!(hint_for_content_type("text/html"), None);
    }

    #[test]
    fn gain_clamps_and_is_noop_at_unity() {
        let mut s = [1000i16, -1000, 30000, -30000];
        apply_gain(&mut s, 2.0);
        assert_eq!(s, [2000, -2000, 32767, -32768]);
        let mut t = [1234i16, -4321];
        apply_gain(&mut t, 1.0);
        assert_eq!(t, [1234, -4321]);
    }

    // --- output dynamics (leveler / compressor / limiter) ---

    fn test_cfg(level: bool, compress: bool) -> DynamicsCfg {
        DynamicsCfg {
            pre_gain: 1.0,
            level,
            target_rms: db_to_lin(-16.0),
            max_boost: db_to_lin(20.0),
            max_cut: db_to_lin(-12.0),
            compress,
            comp_threshold: db_to_lin(-24.0),
            comp_ratio: 3.0,
            comp_makeup: 1.0,
            limit_ceil: db_to_lin(-1.0),
        }
    }

    /// Full-scale-alternating square at `dbfs` → an i16 chunk with RMS = that level.
    fn square(dbfs: f32, n: usize) -> Vec<i16> {
        let amp = (db_to_lin(dbfs) * I16_SCALE) as i16;
        (0..n)
            .map(|i| if i % 2 == 0 { amp } else { -amp })
            .collect()
    }

    fn rms_dbfs(s: &[i16]) -> f32 {
        let ms = s
            .iter()
            .map(|&v| (v as f32 / I16_SCALE).powi(2))
            .sum::<f32>()
            / s.len() as f32;
        20.0 * ms.sqrt().max(1e-9).log10()
    }

    #[test]
    fn leveler_boosts_quiet_toward_target() {
        let mut d = Dynamics::new(test_cfg(true, false), 48_000, 1);
        let mut last = Vec::new();
        for _ in 0..40 {
            let mut c = square(-30.0, 4_800); // ~4 s of quiet content
            d.process(&mut c);
            last = c;
        }
        let out = rms_dbfs(&last);
        assert!(
            out > -20.0 && out < -12.0,
            "quiet -30 dBFS leveled to {out} dBFS (target -16)"
        );
        assert!(
            d.gain_db() > 8.0,
            "expected a real boost, got {} dB",
            d.gain_db()
        );
    }

    #[test]
    fn limiter_never_clips_past_ceiling() {
        // Force a large boost so the leveler drives the signal well past the ceiling.
        let mut cfg = test_cfg(true, false);
        cfg.target_rms = 2.0; // absurd target → maximal boost
        cfg.max_boost = db_to_lin(40.0);
        let mut d = Dynamics::new(cfg, 48_000, 1);
        let ceil = (db_to_lin(-1.0) * I16_SCALE) as i16;
        let mut maxabs = 0i16;
        for _ in 0..50 {
            let mut c = square(-10.0, 4_800);
            d.process(&mut c);
            for &s in &c {
                maxabs = maxabs.max(s.saturating_abs());
            }
        }
        assert!(
            maxabs <= ceil + 2,
            "peak {maxabs} exceeded limiter ceiling {ceil}"
        );
    }

    #[test]
    fn compressor_reduces_level_above_threshold() {
        let mut d = Dynamics::new(test_cfg(false, true), 48_000, 1);
        let mut last = Vec::new();
        for _ in 0..30 {
            let mut c = square(-6.0, 4_800); // loud, well above the -24 dBFS threshold
            d.process(&mut c);
            last = c;
        }
        let out = rms_dbfs(&last);
        assert!(
            out < -12.0,
            "compressor should pull -6 dBFS down, got {out} dBFS"
        );
    }

    #[test]
    fn noop_config_matches_apply_gain() {
        let mut cfg = test_cfg(false, false);
        cfg.pre_gain = 2.0;
        let mut d = Dynamics::new(cfg, 48_000, 1);
        let mut a = [1000i16, -1000, 30000, -30000];
        let mut b = a;
        d.process(&mut a);
        apply_gain(&mut b, 2.0);
        assert_eq!(a, b);
    }
}
