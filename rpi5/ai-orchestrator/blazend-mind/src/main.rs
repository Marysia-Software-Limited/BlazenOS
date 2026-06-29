//! `blazend-mind` — the conversation mind (Phase 4).
//!
//! Subscribes to `nlu.miss` (utterances the fast-path router didn't match —
//! free-form chat), and for each one builds a `brain.request` via the **shared**
//! `jessica_core::Mind`: persona + the user's name + relevant notes (the system
//! prompt) and the chosen backend (`RoutePlan`). It publishes that request for
//! the Python ML-glue inference server, which runs the model and answers with
//! `brain.reply` (spoken by `blazend-tts`, observed here).
//!
//! This is the appliance adapter around the **device-independent mind** — the
//! same `jessica-core` logic the iOS/Android apps use via `jessica-ffi`. The
//! mind decides *what to say*; running the model is ML glue (Python). See
//! `docs/14-RUST-PYTHON-SPLIT.md` §1 and `docs/19-DOMAIN-ARCHITECTURE.md` Phase 4.

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;

use blazend_ipc::{runtime_dir, Event, EventEnvelope, Publisher, Subscriber};
use clap::Parser;
use jessica_core::{Backend, InMemoryStore, Mind, RoutePlan};

#[derive(Parser, Debug)]
#[command(name = "blazend-mind", version)]
struct Args {}

/// Whether a backend is reachable, from the environment. Mirrors the Pi's
/// `registry.select_chat_llm` preference inputs: on-device Bielik is always
/// available; the LAN Ollama box and the cloud tiers gate on their env vars.
fn backend_available(b: Backend) -> bool {
    let set = |k: &str| {
        std::env::var(k)
            .map(|v| !v.trim().is_empty())
            .unwrap_or(false)
    };
    match b {
        Backend::Local => true, // Bielik baked into the image
        Backend::Ollama => set("BLAZEN_LLM_OLLAMA_URL"),
        Backend::OpenAi => set("OPENAI_API_KEY"),
        Backend::Gemini => set("GEMINI_API_KEY"),
    }
}

/// Pure planning step: an `nlu.miss` turn → a `brain.request` event. Built over
/// the shared `Mind`, so it is side-effect-free and unit-testable (the
/// backend-availability predicate is injected, not read from the environment).
fn plan_turn(
    mind: &Mind,
    store: &InMemoryStore,
    plan: &RoutePlan,
    request_id: String,
    language: &str,
    transcript: &str,
    available: impl Fn(Backend) -> bool,
) -> Event {
    let req = mind.plan_chat(request_id, transcript, language, store, plan, available);
    Event::BrainRequest {
        request_id: req.request_id,
        language: req.language,
        prompt: req.prompt,
        system: Some(req.system),
        backend: req.backend.map(|b| b.as_str().to_string()),
    }
}

/// Connect to the NLU publisher (`nlu.miss` / `nlu.intent`), retrying.
async fn connect_nlu(nlu_sock: &Path) -> Subscriber {
    loop {
        match Subscriber::connect(nlu_sock).await {
            Ok(s) => return s,
            Err(_) => tokio::time::sleep(Duration::from_millis(200)).await,
        }
    }
}

/// Run the mind loop: read `nlu.miss`, publish `brain.request`.
async fn run(
    nlu_sock: PathBuf,
    mind_sock: PathBuf,
    mind: Mind,
    store: InMemoryStore,
) -> anyhow::Result<()> {
    let plan = RoutePlan::default_chat();
    let publisher = Publisher::bind(&mind_sock).await?;
    tracing::info!(socket = ?publisher.socket_path, "mind online");
    let mut sub = connect_nlu(&nlu_sock).await;
    tracing::info!(nlu = ?nlu_sock, "mind subscribed to nlu.miss");
    let seq = AtomicU64::new(0);
    while let Some(env) = sub.next().await? {
        if let Event::NluMiss {
            language,
            transcript,
        } = env.event
        {
            let request_id = format!("mind-{}", seq.fetch_add(1, Ordering::Relaxed));
            let req = plan_turn(
                &mind,
                &store,
                &plan,
                request_id,
                &language,
                &transcript,
                backend_available,
            );
            if let Event::BrainRequest {
                ref request_id,
                ref backend,
                ..
            } = req
            {
                tracing::info!(%request_id, backend = backend.as_deref().unwrap_or("none"), %language, %transcript, "chat turn → brain.request");
            }
            publisher
                .publish(EventEnvelope::new("blazend-mind", env.ts_ms, req))
                .await?;
        }
    }
    Ok(())
}

#[tokio::main(flavor = "current_thread")]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()),
        )
        .init();
    let _args = Args::parse();
    // Context store is empty for now; JSON persistence (parity with the Python
    // memory.json) lands in the next step, giving the mind the user's name + notes.
    run(
        runtime_dir().join("nlu.sock"),
        runtime_dir().join("mind.sock"),
        Mind::new(),
        InMemoryStore::new(),
    )
    .await
}

#[cfg(test)]
mod tests {
    use super::*;
    use jessica_core::MemoryStore;

    #[test]
    fn nlu_miss_becomes_brain_request_with_persona() {
        let mind = Mind::new();
        let store = InMemoryStore::new();
        let plan = RoutePlan::default_chat();
        let ev = plan_turn(
            &mind,
            &store,
            &plan,
            "mind-0".into(),
            "pl",
            "opowiedz mi coś",
            |b| b == Backend::Local, // deterministic: only on-device available
        );
        match ev {
            Event::BrainRequest {
                request_id,
                language,
                prompt,
                system,
                backend,
            } => {
                assert_eq!(request_id, "mind-0");
                assert_eq!(language, "pl");
                assert_eq!(prompt, "opowiedz mi coś");
                assert!(system.unwrap().contains("Jesteś Jessica"));
                // Local Bielik is always available → first non-LAN backend.
                assert_eq!(backend.as_deref(), Some("local"));
            }
            other => panic!("wrong event: {other:?}"),
        }
    }

    #[test]
    fn injects_user_name_into_system_prompt() {
        let mind = Mind::new();
        let mut store = InMemoryStore::new();
        store.set_profile("name", "Ala", "2026-06-29T10:00:00");
        let plan = RoutePlan::default_chat();
        let ev = plan_turn(&mind, &store, &plan, "r".into(), "pl", "cześć", |_| true);
        if let Event::BrainRequest { system, .. } = ev {
            assert!(system.unwrap().contains("Użytkownik ma na imię Ala."));
        } else {
            panic!("expected brain.request");
        }
    }
}
