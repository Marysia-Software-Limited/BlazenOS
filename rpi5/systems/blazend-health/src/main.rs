//! `blazend-health` — watchdog + recovery-mode controller.
//!
//! Connects to every other unit's socket, watches for liveness, writes
//! `/run/blazen/state.json` periodically and publishes a `health.status`
//! verdict whenever the recovery level changes. The decision logic lives in
//! [`monitor`] (unit-tested); the live peer-socket watching that feeds it the
//! `seen` set is an M8 hook — until then every unit is assumed alive, so the
//! dev host stays `ok`/green.

mod monitor;

use std::collections::HashSet;
use std::time::Duration;

use blazend_ipc::{runtime_dir, Event, EventEnvelope, Publisher};
use clap::Parser;
use serde_json::json;

use monitor::{Level, RecoveryMonitor, Thresholds};

/// The peers the watchdog tracks for liveness.
const WATCHED_UNITS: &[&str] = &[
    "blazend-audio-in",
    "blazend-wake",
    "blazend-asr",
    "blazend-brain",
    "blazend-tts",
    "blazend-audio-out",
    "blazend-orchestrator",
];

#[derive(Parser, Debug)]
#[command(name = "blazend-health", version)]
struct Args {
    /// Don't actually attempt to peer-connect; just heartbeat.
    #[arg(long)]
    mock: bool,
    /// State file to write (default: /run/blazen/state.json or
    /// $BLAZEN_RUNTIME_DIR/state.json).
    #[arg(long)]
    state_path: Option<std::path::PathBuf>,
}

#[tokio::main(flavor = "current_thread")]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()),
        )
        .init();
    let args = Args::parse();
    let publisher = Publisher::bind(runtime_dir().join("health.sock")).await?;
    let state_path = args
        .state_path
        .unwrap_or_else(|| runtime_dir().join("state.json"));
    tracing::info!(socket = ?publisher.socket_path, state = ?state_path, "health online");

    let watched: Vec<String> = WATCHED_UNITS.iter().map(|s| s.to_string()).collect();
    let mut mon = RecoveryMonitor::new(Thresholds::default(), watched.clone());
    let mut last_level: Option<Level> = None;

    let mut tick: u64 = 0;
    loop {
        // M8 hook: the live `seen` set comes from subscribing to each peer's
        // socket and tracking heartbeat arrivals. Until that lands, assume all
        // peers are alive so the dev host reports `ok`/green; the recovery
        // decision logic itself is exercised by monitor's unit tests.
        let seen: HashSet<String> = watched.iter().cloned().collect();
        let verdict = mon.tick(&seen, None, tick / 1000);

        let units: serde_json::Map<String, serde_json::Value> = WATCHED_UNITS
            .iter()
            .map(|u| {
                let status = if *u == verdict.unit && verdict.level != Level::Ok {
                    verdict.action.as_str()
                } else {
                    "running"
                };
                ((*u).to_string(), serde_json::Value::String(status.into()))
            })
            .collect();

        let state = json!({
            "v": 1,
            "ts_ms": tick,
            "ready": true,
            "units": units,
            "hailo":  { "present": false },
            "ssh":    { "enabled": true },
            "led":    verdict.level.led(),
            "health": {
                "level": verdict.level.as_str(),
                "unit": verdict.unit,
                "action": verdict.action.as_str(),
            },
        });
        if let Some(parent) = state_path.parent() {
            tokio::fs::create_dir_all(parent).await.ok();
        }
        tokio::fs::write(&state_path, serde_json::to_vec_pretty(&state)?).await?;

        // Announce a level change on the bus so the orchestrator can drive the
        // LED + a spoken recovery cue.
        if last_level != Some(verdict.level) {
            publisher
                .publish(EventEnvelope::new(
                    "blazend-health",
                    tick,
                    Event::HealthStatus {
                        level: verdict.level.as_str().into(),
                        unit: verdict.unit.clone(),
                        detail: String::new(),
                        action: verdict.action.as_str().into(),
                    },
                ))
                .await?;
            last_level = Some(verdict.level);
        }

        publisher
            .publish(EventEnvelope::new(
                "blazend-health",
                tick,
                Event::SystemEvent {
                    kind: "heartbeat".into(),
                    detail: None,
                },
            ))
            .await?;
        tick += 5_000;
        tokio::time::sleep(Duration::from_secs(5)).await;
    }
}
