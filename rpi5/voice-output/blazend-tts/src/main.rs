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

use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::time::Duration;

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
}

fn ring_path() -> PathBuf {
    runtime_dir().join("tts-ring.shm")
}

fn voice_name(model: &Path) -> String {
    model
        .file_stem()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_else(|| "unknown".into())
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
        .chunks_exact(2)
        .map(|b| i16::from_le_bytes([b[0], b[1]]))
        .collect())
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
    tracing::info!("subscribed to brain.reply on brain.sock + orchestrator.sock");

    loop {
        let envelope = tokio::select! {
            r = brain.next() => r?,
            r = orchestrator.next() => r?,
        };
        let Some(env) = envelope else { continue };
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
        let voice = voice_for(&lang, &args.voice_pl, &args.voice_en);
        // On the Polish voice, spell the assistant's name phonetically so Piper
        // says it like the English "Jessica" (≈ "dżesika") rather than reading
        // "Jessica" with Polish letter values. English voice keeps "Jessica".
        let say = if lang == "en" {
            say
        } else {
            polish_name_phonetics(&say)
        };
        match synthesize(&args.piper, voice, &say).await {
            Ok(pcm) if !pcm.is_empty() => {
                ring.push(&pcm);
                let ts = ring.write_pos() * 1000 / args.sample_rate as u64;
                tracing::info!(lang, samples = pcm.len(), text = %say, "synthesised");
                publisher
                    .publish(EventEnvelope::new(
                        "blazend-tts",
                        ts,
                        Event::TtsFrame {
                            voice: voice_name(voice),
                            samples: pcm.len() as u32,
                        },
                    ))
                    .await?;
            }
            Ok(_) => tracing::warn!("piper produced no audio"),
            Err(e) => tracing::error!("synthesis failed: {e}"),
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
        assert_eq!(voice_name(pl), "pl_PL-gosia-medium");
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
