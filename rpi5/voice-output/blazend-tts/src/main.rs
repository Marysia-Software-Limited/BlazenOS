//! `blazend-tts` — Piper synthesis worker.
//!
//! Real mode (M4): subscribes to `brain.reply` (from both `brain.sock` and the
//! orchestrator's command replies on `orchestrator.sock`), synthesises the text
//! with the Piper voice for its language (Jessica = `pl_PL-gosia-medium` for
//! Polish, `en_US-lessac-medium` for English — see `configs/tts.yaml`), writes
//! the raw i16 PCM into the shared-memory TTS ring (`tts-ring.shm`) that
//! `blazend-audio-out` plays, and publishes `tts.frame {voice, samples}`. Piper
//! runs as an external subprocess (`piper --output-raw`), like any synth tool.
//! `--mock` keeps the M1 no-op behaviour for CI/laptops.

use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::time::{Duration, Instant};

use anyhow::{anyhow, Result};
use blazend_audioring::RingWriter;
use blazend_ipc::{runtime_dir, Event, EventEnvelope, Publisher, Subscriber};
use clap::Parser;
use tokio::io::AsyncWriteExt;

#[derive(Parser, Debug)]
#[command(name = "blazend-tts", version)]
struct Args {
    /// Drop replies instead of synthesising / touching the ring.
    #[arg(long)]
    mock: bool,
    /// Piper executable (the `piper-tts` CLI).
    #[arg(long, default_value = "piper")]
    piper: String,
    /// Polish voice model (Jessica).
    #[arg(long, default_value = "models/tts/pl_PL-gosia-medium.onnx")]
    voice_pl: PathBuf,
    /// English voice model.
    #[arg(long, default_value = "models/tts/en_US-lessac-medium.onnx")]
    voice_en: PathBuf,
    /// Piper output sample rate (medium voices are 22050 Hz).
    #[arg(long, default_value_t = 22_050)]
    sample_rate: u32,
    /// TTS ring length in seconds (must hold one full reply).
    #[arg(long, default_value_t = 15)]
    ring_seconds: u32,
    /// Remote XTTS endpoint (paul's GPU) for Jessica's rich voice. Empty = Piper
    /// only. Mirrors the mesh `tts.xtts` resource; set via BLAZEN_TTS_XTTS_URL.
    #[arg(long, default_value = "")]
    xtts_url: String,
    /// XTTS built-in speaker — Jessica's one voice (also the audiobook narrator).
    #[arg(long, default_value = "Ana Florence")]
    xtts_speaker: String,
    /// Voice cache dir: pre-rendered/self-warmed XTTS phrases as raw i16 PCM at the
    /// ring rate, so her voice works OFFLINE. See scripts/render-voicebank.py.
    #[arg(long, default_value = "/var/lib/blazen/voice-cache")]
    voice_cache: PathBuf,
}

fn ring_path() -> PathBuf {
    runtime_dir().join("tts-ring.shm")
}

/// Pick the voice for a reply language — Polish-first (anything but `en` →
/// Jessica's Polish voice).
fn voice_for<'a>(lang: &str, pl: &'a Path, en: &'a Path) -> &'a Path {
    if lang == "en" {
        en
    } else {
        pl
    }
}

/// Respell the assistant's name for the Polish voice so Piper pronounces it like
/// the English "Jessica" (≈ "dżesika") instead of with Polish letter values.
/// Longer forms are replaced before shorter ones so "Jessica" doesn't degrade to
/// "Dżesica" via the "Jess" rule; both capitalised and lower-case forms (plus the
/// vocative "Jessico" and short "Jess") are covered.
fn polish_name_phonetics(text: &str) -> String {
    text.replace("Jessica", "Dżesika")
        .replace("jessica", "dżesika")
        .replace("Jessico", "Dżesiko")
        .replace("jessico", "dżesiko")
        .replace("Jess", "Dżes")
        .replace("jess", "dżes")
}

/// Run Piper on `text`, returning raw mono i16 PCM at the voice's sample rate.
async fn synthesize(piper: &str, voice: &Path, text: &str) -> Result<Vec<i16>> {
    let mut child = tokio::process::Command::new(piper)
        .arg("-m")
        .arg(voice)
        .arg("--output-raw")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()?;
    let mut stdin = child.stdin.take().ok_or_else(|| anyhow!("piper stdin"))?;
    stdin.write_all(text.as_bytes()).await?;
    drop(stdin); // EOF → piper synthesises and writes stdout
    let output = child.wait_with_output().await?;
    if !output.status.success() {
        return Err(anyhow!("piper exited {:?}", output.status.code()));
    }
    Ok(output
        .stdout
        .as_chunks::<2>()
        .0
        .iter()
        .map(|b| i16::from_le_bytes(*b))
        .collect())
}

/// FNV-1a 64-bit hex — the voice-cache key. MUST match the identical hash in
/// `scripts/render-voicebank.py` so pre-rendered phrases are found.
fn fnv1a_hex(s: &str) -> String {
    let mut h: u64 = 0xcbf2_9ce4_8422_2325;
    for b in s.as_bytes() {
        h ^= u64::from(*b);
        h = h.wrapping_mul(0x0000_0100_0000_01b3);
    }
    format!("{h:016x}")
}

/// `<cache>/<lang>/<fnv(text|lang|speaker)>.pcm` — raw mono i16 LE at the ring rate.
fn cache_path(dir: &Path, lang: &str, text: &str, speaker: &str) -> PathBuf {
    let key = fnv1a_hex(&format!("{text}|{lang}|{speaker}"));
    dir.join(lang).join(format!("{key}.pcm"))
}

fn read_pcm(path: &Path) -> Option<Vec<i16>> {
    let bytes = std::fs::read(path).ok()?;
    if bytes.len() < 2 {
        return None;
    }
    Some(
        bytes
            .as_chunks::<2>()
            .0
            .iter()
            .map(|b| i16::from_le_bytes(*b))
            .collect(),
    )
}

fn write_pcm(path: &Path, pcm: &[i16]) {
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let mut bytes = Vec::with_capacity(pcm.len() * 2);
    for s in pcm {
        bytes.extend_from_slice(&s.to_le_bytes());
    }
    let tmp = path.with_extension("pcm.tmp");
    if std::fs::write(&tmp, &bytes).is_ok() {
        let _ = std::fs::rename(&tmp, path);
    }
}

/// Parse a RIFF/PCM16 WAV → (mono i16 samples, sample_rate); downmixes stereo.
fn parse_wav(bytes: &[u8]) -> Option<(Vec<i16>, u32)> {
    if bytes.len() < 44 || &bytes[0..4] != b"RIFF" || &bytes[8..12] != b"WAVE" {
        return None;
    }
    let (mut rate, mut channels): (u32, u16) = (0, 1);
    let mut data: Option<&[u8]> = None;
    let mut i = 12;
    while i + 8 <= bytes.len() {
        let id = &bytes[i..i + 4];
        let sz =
            u32::from_le_bytes([bytes[i + 4], bytes[i + 5], bytes[i + 6], bytes[i + 7]]) as usize;
        let (bs, be) = (i + 8, (i + 8 + sz).min(bytes.len()));
        if id == b"fmt " && be - bs >= 16 {
            channels = u16::from_le_bytes([bytes[bs + 2], bytes[bs + 3]]);
            rate = u32::from_le_bytes([bytes[bs + 4], bytes[bs + 5], bytes[bs + 6], bytes[bs + 7]]);
        } else if id == b"data" {
            data = Some(&bytes[bs..be]);
        }
        i = bs + sz + (sz & 1); // chunks are word-aligned
    }
    let data = data?;
    if rate == 0 {
        return None;
    }
    let mut samples: Vec<i16> = data
        .as_chunks::<2>()
        .0
        .iter()
        .map(|b| i16::from_le_bytes(*b))
        .collect();
    if channels == 2 {
        samples = samples
            .as_chunks::<2>()
            .0
            .iter()
            .map(|s| ((i32::from(s[0]) + i32::from(s[1])) / 2) as i16)
            .collect();
    }
    Some((samples, rate))
}

/// Linear-resample mono i16 from `src` to `dst` Hz.
fn resample_i16(input: &[i16], src: u32, dst: u32) -> Vec<i16> {
    if src == dst || input.is_empty() {
        return input.to_vec();
    }
    let ratio = f64::from(src) / f64::from(dst);
    let out_len = (input.len() as f64 / ratio) as usize;
    let last = input.len() - 1;
    let mut out = Vec::with_capacity(out_len);
    for i in 0..out_len {
        let pos = i as f64 * ratio;
        let idx = pos as usize;
        let frac = pos - idx as f64;
        let a = f64::from(input[idx.min(last)]);
        let b = f64::from(input[(idx + 1).min(last)]);
        out.push((a + (b - a) * frac).round() as i16);
    }
    out
}

/// Render `text` via the remote XTTS → mono i16 at `ring_rate`. Blocking ureq →
/// call under `spawn_blocking`.
fn xtts_render_blocking(
    url: &str,
    speaker: &str,
    lang: &str,
    text: &str,
    ring_rate: u32,
) -> Result<Vec<i16>> {
    let body = serde_json::json!({"text": text, "language": lang, "speaker": speaker}).to_string();
    let resp = ureq::post(url)
        .timeout(Duration::from_secs(20))
        .set("Content-Type", "application/json")
        .send_string(&body)
        .map_err(|e| anyhow!("xtts request: {e}"))?;
    let mut buf = Vec::new();
    resp.into_reader().read_to_end(&mut buf)?;
    let (samples, rate) =
        parse_wav(&buf).ok_or_else(|| anyhow!("xtts: not a WAV ({} bytes)", buf.len()))?;
    Ok(resample_i16(&samples, rate, ring_rate))
}

/// Resolve reply text → PCM at the ring rate, in Jessica's ONE voice when possible:
/// **cache** (offline XTTS) → **live XTTS** (self-warms the cache) → **Piper** (the
/// always-available local floor). Piper keeps its phonetic name respelling; the
/// XTTS/cache path speaks the text verbatim (its key must match the render tool).
async fn resolve<'a>(
    args: &'a Args,
    lang: &str,
    say: &str,
    xtts_down_until: &mut Option<Instant>,
) -> (Vec<i16>, &'a str) {
    let cpath = cache_path(&args.voice_cache, lang, say, &args.xtts_speaker);
    if let Some(pcm) = read_pcm(&cpath) {
        if !pcm.is_empty() {
            return (pcm, "cache");
        }
    }
    let xtts_ok = xtts_down_until.is_none_or(|t| Instant::now() >= t);
    if !args.xtts_url.is_empty() && xtts_ok {
        let (url, sp, l, t, rate) = (
            args.xtts_url.clone(),
            args.xtts_speaker.clone(),
            lang.to_string(),
            say.to_string(),
            args.sample_rate,
        );
        match tokio::task::spawn_blocking(move || xtts_render_blocking(&url, &sp, &l, &t, rate))
            .await
        {
            Ok(Ok(pcm)) if !pcm.is_empty() => {
                write_pcm(&cpath, &pcm); // self-warm so it's offline next time
                *xtts_down_until = None;
                return (pcm, "xtts");
            }
            Ok(Err(e)) => {
                tracing::warn!("xtts unavailable ({e}); Piper fallback");
                *xtts_down_until = Some(Instant::now() + Duration::from_secs(30));
            }
            Err(e) => tracing::warn!("xtts task join: {e}"),
            _ => {}
        }
    }
    let voice = voice_for(lang, &args.voice_pl, &args.voice_en);
    let piper_text = if lang == "en" {
        say.to_string()
    } else {
        polish_name_phonetics(say)
    };
    match synthesize(&args.piper, voice, &piper_text).await {
        Ok(pcm) => (pcm, "piper"),
        Err(e) => {
            tracing::error!("piper failed: {e}");
            (Vec::new(), "piper")
        }
    }
}

async fn connect(sock: PathBuf) -> Subscriber {
    loop {
        if sock.exists() {
            if let Ok(sub) = Subscriber::connect(&sock).await {
                return sub;
            }
        }
        tokio::time::sleep(Duration::from_millis(200)).await;
    }
}

async fn run_real(publisher: &Publisher, args: &Args) -> Result<()> {
    let rt = runtime_dir();
    let mut ring = RingWriter::create(
        ring_path(),
        args.sample_rate,
        1,
        args.sample_rate * args.ring_seconds,
    )?;
    tracing::info!(ring = ?ring_path(), pl = ?args.voice_pl, en = ?args.voice_en, "tts real path ready");

    let mut brain = connect(rt.join("brain.sock")).await;
    let mut orchestrator = connect(rt.join("orchestrator.sock")).await;
    tracing::info!(xtts = %if args.xtts_url.is_empty() { "off" } else { &args.xtts_url },
        cache = ?args.voice_cache, "subscribed to brain.reply; voice: cache→xtts→piper");
    let mut xtts_down_until: Option<Instant> = None;

    loop {
        // A broken upstream must never kill the voice: `Err` (framing garbage —
        // live 2026-08-21: "frame too large: {\"a…" exited the daemon) and
        // `Ok(None)` (publisher EOF — the old `continue` busy-polled the dead
        // stream at 100% CPU after every brain restart) both mean the same
        // thing: drop the stream and reconnect.
        enum Src {
            Brain,
            Orchestrator,
        }
        let (src, res) = tokio::select! {
            r = brain.next() => (Src::Brain, r),
            r = orchestrator.next() => (Src::Orchestrator, r),
        };
        let env = match res {
            Ok(Some(env)) => env,
            Ok(None) | Err(_) => {
                if let Err(e) = &res {
                    tracing::warn!(error = %e, "upstream stream broken — reconnecting");
                }
                match src {
                    Src::Brain => brain = connect(rt.join("brain.sock")).await,
                    Src::Orchestrator => {
                        orchestrator = connect(rt.join("orchestrator.sock")).await;
                    }
                }
                continue;
            }
        };
        let Event::BrainReply {
            chunk,
            final_,
            language,
            text,
            ..
        } = env.event
        else {
            continue;
        };
        if !final_ {
            continue; // speak whole utterances, not mid-stream chunks
        }
        let say = text.unwrap_or(chunk);
        if say.trim().is_empty() {
            continue;
        }
        let lang = language.unwrap_or_else(|| "pl".into());
        // Jessica's ONE voice when possible: cache → live XTTS → Piper floor.
        let (pcm, backend) = resolve(args, &lang, &say, &mut xtts_down_until).await;
        if pcm.is_empty() {
            tracing::warn!(backend, "no audio for reply");
            continue;
        }
        ring.push(&pcm);
        let ts = ring.write_pos() * 1000 / u64::from(args.sample_rate);
        tracing::info!(lang, backend, samples = pcm.len(), text = %say, "spoke");
        publisher
            .publish(EventEnvelope::new(
                "blazend-tts",
                ts,
                Event::TtsFrame {
                    voice: backend.to_string(),
                    samples: pcm.len() as u32,
                },
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
    let mut args = Args::parse();
    // The XTTS endpoint is set by the service unit (mirrors the mesh tts.xtts).
    if args.xtts_url.is_empty() {
        if let Ok(u) = std::env::var("BLAZEN_TTS_XTTS_URL") {
            args.xtts_url = u;
        }
    }
    if let Ok(s) = std::env::var("BLAZEN_TTS_XTTS_SPEAKER") {
        if !s.is_empty() {
            args.xtts_speaker = s;
        }
    }
    let publisher = Publisher::bind(runtime_dir().join("tts.sock")).await?;
    publisher
        .publish(EventEnvelope::new(
            "blazend-tts",
            0,
            Event::SystemEvent {
                kind: "ready".into(),
                detail: None,
            },
        ))
        .await?;
    tracing::info!(socket = ?publisher.socket_path, "tts online");

    if args.mock {
        loop {
            tokio::time::sleep(Duration::from_secs(60)).await;
        }
    }
    run_real(&publisher, &args).await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn voice_selection_is_polish_first() {
        let pl = Path::new("models/tts/pl_PL-gosia-medium.onnx");
        let en = Path::new("models/tts/en_US-lessac-medium.onnx");
        assert_eq!(voice_for("pl", pl, en), pl);
        assert_eq!(voice_for("en", pl, en), en);
        assert_eq!(voice_for("de", pl, en), pl); // unknown → Jessica's Polish voice
    }

    #[test]
    fn fnv1a_is_stable_and_matches_python() {
        // hashlib-free reference: FNV-1a 64 of "cześć|pl|Ana Florence".
        // (Kept in sync with scripts/render-voicebank.py's `_key`.)
        let k = fnv1a_hex("cześć|pl|Ana Florence");
        assert_eq!(k.len(), 16);
        assert_eq!(fnv1a_hex("a"), "af63dc4c8601ec8c"); // canonical FNV-1a-64("a")
        assert_ne!(fnv1a_hex("Nie zrozumiałam.|pl|Ana Florence"), k);
    }

    #[test]
    fn cache_path_is_lang_scoped_and_keyed() {
        let p = cache_path(Path::new("/cache"), "pl", "Słucham?", "Ana Florence");
        assert!(p.starts_with("/cache/pl/"));
        assert_eq!(p.extension().unwrap(), "pcm");
    }

    #[test]
    fn resample_shrinks_length_by_rate_ratio() {
        let input: Vec<i16> = (0..2400).map(|i| (i % 100) as i16).collect();
        let out = resample_i16(&input, 24_000, 22_050);
        // 24k → 22.05k ≈ 0.919× the samples
        assert!((out.len() as i64 - 2205).abs() <= 2, "got {}", out.len());
        assert_eq!(resample_i16(&input, 22_050, 22_050), input); // no-op at same rate
    }

    #[test]
    fn parse_wav_roundtrips_mono_pcm16() {
        // hand-build a tiny mono 22050 Hz PCM16 WAV with 3 samples
        let samples: [i16; 3] = [1000, -2000, 3000];
        let mut w = Vec::new();
        w.extend_from_slice(b"RIFF");
        w.extend_from_slice(&(36u32 + 6).to_le_bytes());
        w.extend_from_slice(b"WAVEfmt ");
        w.extend_from_slice(&16u32.to_le_bytes());
        w.extend_from_slice(&1u16.to_le_bytes()); // PCM
        w.extend_from_slice(&1u16.to_le_bytes()); // mono
        w.extend_from_slice(&22_050u32.to_le_bytes());
        w.extend_from_slice(&44_100u32.to_le_bytes()); // byte rate
        w.extend_from_slice(&2u16.to_le_bytes()); // block align
        w.extend_from_slice(&16u16.to_le_bytes()); // bits
        w.extend_from_slice(b"data");
        w.extend_from_slice(&6u32.to_le_bytes());
        for s in samples {
            w.extend_from_slice(&s.to_le_bytes());
        }
        let (got, rate) = parse_wav(&w).expect("parse");
        assert_eq!(rate, 22_050);
        assert_eq!(got, samples);
    }

    #[test]
    fn polish_name_is_respelled_phonetically() {
        assert_eq!(
            polish_name_phonetics("Cześć, tu Jessica. Jestem gotowa."),
            "Cześć, tu Dżesika. Jestem gotowa."
        );
        assert_eq!(polish_name_phonetics("Tak, Jessico?"), "Tak, Dżesiko?");
        assert_eq!(polish_name_phonetics("mówi jessica"), "mówi dżesika");
        // "Jessica" must not degrade to "Dżesica" via the short-form rule.
        assert!(!polish_name_phonetics("Jessica").contains("Dżesica"));
        // Polish text without the name is untouched.
        assert_eq!(polish_name_phonetics("Która godzina?"), "Która godzina?");
    }
}
