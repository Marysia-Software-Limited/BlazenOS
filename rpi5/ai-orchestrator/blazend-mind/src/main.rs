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
use jessica_core::{Backend, Dispatch, InMemoryStore, Mind, RoutePlan};
use tokio::sync::mpsc;

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

/// Where the Pi persists memory (mirrors the Python `context.data_dir()`):
/// `$BLAZEN_DATA_DIR/memory.json`, else `<runtime>/data/memory.json`.
fn memory_path() -> PathBuf {
    if let Ok(d) = std::env::var("BLAZEN_DATA_DIR") {
        return PathBuf::from(d).join("memory.json");
    }
    runtime_dir().join("data").join("memory.json")
}

/// Connect to a publisher socket, retrying until it appears.
async fn connect(sock: &Path) -> Subscriber {
    loop {
        match Subscriber::connect(sock).await {
            Ok(s) => return s,
            Err(_) => tokio::time::sleep(Duration::from_millis(200)).await,
        }
    }
}

/// Funnel a subscriber's envelopes into `tx` until EOF (in a task, so the mind
/// can merge several input sockets — nlu + tool responses — in one loop).
fn spawn_reader(sock: PathBuf, tx: mpsc::Sender<EventEnvelope>) {
    tokio::spawn(async move {
        let mut sub = connect(&sock).await;
        while let Ok(Some(env)) = sub.next().await {
            if tx.send(env).await.is_err() {
                break;
            }
        }
    });
}

fn load_store(mem_path: &Path) -> InMemoryStore {
    InMemoryStore::load_json(mem_path).unwrap_or_else(|e| {
        tracing::warn!(error = %e, "memory load failed; using empty store");
        InMemoryStore::new()
    })
}

/// Run the mind loop. Merges `nlu.miss`/`nlu.intent` (from the NLU) and
/// `tool.response` (from the tool server), and publishes `brain.request`
/// (chat), `tool.request` (commands), and `brain.reply` (the spoken reply for
/// a tool result) on `mind_sock`.
async fn run(
    nlu_sock: PathBuf,
    tools_sock: PathBuf,
    mind_sock: PathBuf,
    mind: Mind,
    mem_path: PathBuf,
) -> anyhow::Result<()> {
    let plan = RoutePlan::default_chat();
    let publisher = Publisher::bind(&mind_sock).await?;
    tracing::info!(socket = ?publisher.socket_path, memory = ?mem_path, "mind online");
    let (tx, mut rx) = mpsc::channel::<EventEnvelope>(64);
    spawn_reader(nlu_sock, tx.clone()); // nlu.miss / nlu.intent
    spawn_reader(tools_sock, tx); // tool.response
    let seq = AtomicU64::new(0);
    let next_id = || format!("mind-{}", seq.fetch_add(1, Ordering::Relaxed));

    while let Some(env) = rx.recv().await {
        let ts = env.ts_ms;
        match env.event {
            // Free-form chat → ask the model.
            Event::NluMiss {
                language,
                transcript,
            } => {
                let store = load_store(&mem_path);
                let req = plan_turn(
                    &mind,
                    &store,
                    &plan,
                    next_id(),
                    &language,
                    &transcript,
                    backend_available,
                );
                if let Event::BrainRequest { ref backend, .. } = req {
                    tracing::info!(backend = backend.as_deref().unwrap_or("none"), %language, %transcript, "chat → brain.request");
                }
                publisher
                    .publish(EventEnvelope::new("blazend-mind", ts, req))
                    .await?;
            }
            // A matched command → route to a tool (if tool-backed).
            Event::NluIntent {
                intent,
                language,
                params,
                ..
            } => {
                if let Dispatch::Tool(call) = jessica_core::dispatch(&intent, &params) {
                    let request_id = next_id();
                    tracing::info!(%request_id, %intent, tool = %call.tool, "command → tool.request");
                    publisher
                        .publish(EventEnvelope::new(
                            "blazend-mind",
                            ts,
                            Event::ToolRequest {
                                request_id,
                                tool: call.tool,
                                language,
                                args: call.args,
                            },
                        ))
                        .await?;
                }
                // else: not a tool-backed command — config/clock dispatch owns it.
            }
            // A tool's result → speak it (and carry any side effect).
            Event::ToolResponse {
                request_id,
                text,
                language,
                action,
                payload,
                ..
            } => {
                tracing::info!(%request_id, action = action.as_deref().unwrap_or(""), "tool.response → brain.reply");
                publisher
                    .publish(EventEnvelope::new(
                        "blazend-mind",
                        ts,
                        Event::BrainReply {
                            chunk: text.clone(),
                            final_: true,
                            text: Some(text),
                            language,
                            request_id: Some(request_id),
                            action,
                            payload,
                        },
                    ))
                    .await?;
            }
            _ => {}
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
    let rt = runtime_dir();
    run(
        rt.join("nlu.sock"),
        rt.join("tools.sock"),
        rt.join("mind.sock"),
        Mind::new(),
        memory_path(),
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

    /// End-to-end IPC: a command intent → `tool.request`, and a `tool.response`
    /// → `brain.reply`, both over the real wire through a live `run()` loop.
    #[tokio::test]
    async fn nlu_intent_routes_to_tool_and_tool_response_is_spoken() {
        let dir = std::env::temp_dir().join(format!("blazend-mind-test-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let nlu = dir.join("nlu.sock");
        let tools = dir.join("tools.sock");
        let mind_sock = dir.join("mind.sock");

        // Stand-ins for the NLU and the tool server.
        let nlu_pub = Publisher::bind(&nlu).await.unwrap();
        let tools_pub = Publisher::bind(&tools).await.unwrap();
        tokio::time::sleep(Duration::from_millis(30)).await;

        let (n, t, m) = (nlu.clone(), tools.clone(), mind_sock.clone());
        tokio::spawn(async move {
            run(n, t, m, Mind::new(), dir.join("data/memory.json"))
                .await
                .unwrap()
        });
        tokio::time::sleep(Duration::from_millis(400)).await;
        let mut out = Subscriber::connect(&mind_sock).await.unwrap();
        tokio::time::sleep(Duration::from_millis(50)).await;

        // A weather command → tool.request carrying the place slot.
        let mut params = std::collections::HashMap::new();
        params.insert("place".to_string(), "Gdańsk".to_string());
        nlu_pub
            .publish(EventEnvelope::new(
                "blazend-nlu",
                1,
                Event::NluIntent {
                    intent: "weather_query".into(),
                    language: "pl".into(),
                    params,
                    transcript: "jaka pogoda w Gdańsku".into(),
                },
            ))
            .await
            .unwrap();
        let env = tokio::time::timeout(Duration::from_secs(2), out.next())
            .await
            .expect("timed out")
            .unwrap()
            .unwrap();
        let rid = match env.event {
            Event::ToolRequest {
                tool,
                language,
                args,
                request_id,
            } => {
                assert_eq!(tool, "weather.query");
                assert_eq!(language, "pl");
                assert_eq!(args["place"], "Gdańsk");
                request_id
            }
            other => panic!("expected tool.request, got {other:?}"),
        };

        // The tool answers → the mind speaks it as brain.reply.
        tools_pub
            .publish(EventEnvelope::new(
                "blazend-tools",
                2,
                Event::ToolResponse {
                    request_id: rid.clone(),
                    ok: true,
                    text: "Gdańsk: 12°C.".into(),
                    language: Some("pl".into()),
                    action: Some("weather".into()),
                    payload: None,
                },
            ))
            .await
            .unwrap();
        let env = tokio::time::timeout(Duration::from_secs(2), out.next())
            .await
            .expect("timed out")
            .unwrap()
            .unwrap();
        match env.event {
            Event::BrainReply {
                text, request_id, ..
            } => {
                assert_eq!(text.as_deref(), Some("Gdańsk: 12°C."));
                assert_eq!(request_id.as_deref(), Some(rid.as_str()));
            }
            other => panic!("expected brain.reply, got {other:?}"),
        }
    }
}
