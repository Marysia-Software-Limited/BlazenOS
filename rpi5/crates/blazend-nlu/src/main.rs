//! `blazend-nlu` — fast-path intent router.
//!
//! Subscribes to `asr.final` (from `blazend-asr`), runs the shared
//! `jessica-core` regex intent router over the bilingual (EN+PL)
//! `configs/intents/system.yaml`, and publishes `nlu.intent` for every
//! match. Action/tool/confirm are resolved downstream by the orchestrator
//! from the same YAML, so the wire event carries only intent + language +
//! params + transcript. Misses are silent — the orchestrator falls back to
//! the LLM `brain`.
//!
//! This is the **appliance's consumer of the shared Rust core**: the exact
//! same `IntentRouter` that powers the iOS/Android apps via `jessica-ffi`.
//! See docs/14-RUST-PYTHON-SPLIT.md and docs/17-MOBILE-MONOREPO.md.

use std::path::{Path, PathBuf};
use std::time::Duration;

use blazend_ipc::{Event, EventEnvelope, Publisher, Subscriber, runtime_dir};
use clap::Parser;
use jessica_core::IntentRouter;

#[derive(Parser, Debug)]
#[command(name = "blazend-nlu", version)]
struct Args {
    /// Path to the intents YAML. Defaults to
    /// `$BLAZEN_CONFIG_ROOT/intents/system.yaml`, else the on-device
    /// `/etc/blazen/intents/system.yaml`.
    #[arg(long)]
    intents: Option<PathBuf>,
}

fn default_intents_path() -> PathBuf {
    match std::env::var("BLAZEN_CONFIG_ROOT") {
        Ok(root) => PathBuf::from(root).join("intents").join("system.yaml"),
        Err(_) => PathBuf::from("/etc/blazen/intents/system.yaml"),
    }
}

fn load_router(path: &Path) -> anyhow::Result<IntentRouter> {
    let yaml = std::fs::read_to_string(path)
        .map_err(|e| anyhow::anyhow!("reading intents {}: {e}", path.display()))?;
    IntentRouter::from_yaml(&yaml)
        .map_err(|e| anyhow::anyhow!("parsing intents {}: {e}", path.display()))
}

/// Pure routing step: turn an `asr.final` (language, text) into an optional
/// `nlu.intent` event. Side-effect-free so it is trivially unit-tested
/// across EN and PL.
fn route(router: &IntentRouter, language: &str, text: &str) -> Option<Event> {
    let m = router.match_intent(text, language)?;
    Some(Event::NluIntent {
        intent: m.name,
        language: m.language,
        params: m.params,
        transcript: text.to_string(),
    })
}

/// Connect to the ASR publisher socket, retrying until it appears.
async fn connect_asr(asr_sock: &Path) -> Subscriber {
    loop {
        match Subscriber::connect(asr_sock).await {
            Ok(s) => return s,
            Err(_) => tokio::time::sleep(Duration::from_millis(200)).await,
        }
    }
}

/// Run the router loop: read `asr.final`, publish `nlu.intent` on a match.
async fn run(asr_sock: PathBuf, nlu_sock: PathBuf, router: IntentRouter) -> anyhow::Result<()> {
    let publisher = Publisher::bind(&nlu_sock).await?;
    tracing::info!(socket = ?publisher.socket_path, intents = router.len(), "nlu online");
    let mut sub = connect_asr(&asr_sock).await;
    tracing::info!(asr = ?asr_sock, "nlu subscribed to asr.final");
    while let Some(env) = sub.next().await? {
        if let Event::AsrFinal { language, text, .. } = env.event {
            match route(&router, &language, &text) {
                Some(intent_event) => {
                    if let Event::NluIntent { ref intent, .. } = intent_event {
                        tracing::info!(%intent, %language, %text, "intent matched");
                    }
                    publisher
                        .publish(EventEnvelope::new("blazend-nlu", env.ts_ms, intent_event))
                        .await?;
                }
                None => tracing::debug!(%language, %text, "no fast-path intent; deferring to brain"),
            }
        }
    }
    Ok(())
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
    let intents_path = args.intents.unwrap_or_else(default_intents_path);
    let router = load_router(&intents_path)?;
    run(
        runtime_dir().join("asr.sock"),
        runtime_dir().join("nlu.sock"),
        router,
    )
    .await
}

#[cfg(test)]
mod tests {
    use super::*;

    const TEST_INTENTS: &str = r#"
version: 1
intents:
  - name: time_query
    triggers:
      en: ["what time is it", "what's the time"]
      pl: ["która godzina", "która jest godzina"]
    action: tool_call
    tool: clock.time
  - name: volume_set
    triggers:
      en: ['set (the )?volume to (?P<value>\d{1,3})']
      pl: ['ustaw głośność na (?P<value>\d{1,3})']
    action: mutate
"#;

    fn router() -> IntentRouter {
        IntentRouter::from_yaml(TEST_INTENTS).expect("test intents parse")
    }

    #[test]
    fn routes_en_intent() {
        let r = router();
        let ev = route(&r, "en", "what time is it").expect("EN match");
        match ev {
            Event::NluIntent { intent, language, transcript, .. } => {
                assert_eq!(intent, "time_query");
                assert_eq!(language, "en");
                assert_eq!(transcript, "what time is it");
            }
            other => panic!("wrong event: {other:?}"),
        }
    }

    #[test]
    fn routes_pl_intent_with_params() {
        let r = router();
        let ev = route(&r, "pl", "ustaw głośność na 30").expect("PL match");
        match ev {
            Event::NluIntent { intent, language, params, .. } => {
                assert_eq!(intent, "volume_set");
                assert_eq!(language, "pl");
                assert_eq!(params.get("value").map(String::as_str), Some("30"));
            }
            other => panic!("wrong event: {other:?}"),
        }
    }

    #[test]
    fn miss_returns_none() {
        let r = router();
        assert!(route(&r, "en", "tell me a story about dragons").is_none());
    }

    /// End-to-end IPC path: a stand-in ASR publisher feeds `asr.final` into a
    /// live `run()` loop; we assert the matching `nlu.intent` comes out on the
    /// nlu socket. Exercises both EN and PL through the real wire format.
    #[tokio::test]
    async fn ipc_asr_final_to_nlu_intent() {
        let dir = std::env::temp_dir().join(format!("blazend-nlu-test-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let asr_sock = dir.join("asr.sock");
        let nlu_sock = dir.join("nlu.sock");

        // Stand-in for blazend-asr.
        let asr_pub = Publisher::bind(&asr_sock).await.unwrap();
        tokio::time::sleep(Duration::from_millis(30)).await;

        // The unit under test.
        let r = router();
        let asr_c = asr_sock.clone();
        let nlu_c = nlu_sock.clone();
        tokio::spawn(async move { run(asr_c, nlu_c, r).await.unwrap() });

        // Let run() bind nlu.sock and connect to asr.sock.
        tokio::time::sleep(Duration::from_millis(400)).await;

        // Stand-in for the orchestrator: read nlu.intent.
        let mut nlu_sub = Subscriber::connect(&nlu_sock).await.unwrap();
        tokio::time::sleep(Duration::from_millis(50)).await;

        // EN utterance → time_query.
        asr_pub
            .publish(EventEnvelope::new(
                "blazend-asr",
                100,
                Event::AsrFinal {
                    language: "en".into(),
                    text: "what time is it".into(),
                    confidence: 0.9,
                },
            ))
            .await
            .unwrap();

        let env = tokio::time::timeout(Duration::from_secs(3), nlu_sub.next())
            .await
            .expect("nlu.intent timed out")
            .unwrap()
            .unwrap();
        assert_eq!(env.source, "blazend-nlu");
        match env.event {
            Event::NluIntent { intent, language, .. } => {
                assert_eq!(intent, "time_query");
                assert_eq!(language, "en");
            }
            other => panic!("wrong event: {other:?}"),
        }

        // PL utterance with a slot → volume_set / value=45.
        asr_pub
            .publish(EventEnvelope::new(
                "blazend-asr",
                200,
                Event::AsrFinal {
                    language: "pl".into(),
                    text: "ustaw głośność na 45".into(),
                    confidence: 0.9,
                },
            ))
            .await
            .unwrap();

        let env = tokio::time::timeout(Duration::from_secs(3), nlu_sub.next())
            .await
            .expect("PL nlu.intent timed out")
            .unwrap()
            .unwrap();
        match env.event {
            Event::NluIntent { intent, params, .. } => {
                assert_eq!(intent, "volume_set");
                assert_eq!(params.get("value").map(String::as_str), Some("45"));
            }
            other => panic!("wrong event: {other:?}"),
        }

        let _ = std::fs::remove_dir_all(&dir);
    }
}
