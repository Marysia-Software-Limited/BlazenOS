//! IPC event types.
//!
//! Authoritative schemas live under `configs/_schema/events/*.schema.json`
//! and (post-M1) generate `_generated.rs` next to this file. Until the
//! generator is wired in, these hand-written types are the contract;
//! the schema CI gate will be added in M2.

use serde::{Deserialize, Serialize};

use crate::{IpcError, Result, PROTOCOL_VERSION};

/// Every IPC message is wrapped in this envelope.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EventEnvelope {
    /// Protocol version (compared against [`PROTOCOL_VERSION`]).
    pub v: u32,
    /// Monotonic-since-boot timestamp in milliseconds.
    pub ts_ms: u64,
    /// Originating unit name (`blazend-wake`, etc.).
    pub source: String,
    /// Event payload.
    #[serde(flatten)]
    pub event: Event,
}

impl EventEnvelope {
    /// Create a new envelope with the current protocol version.
    pub fn new(source: impl Into<String>, ts_ms: u64, event: Event) -> Self {
        Self {
            v: PROTOCOL_VERSION,
            ts_ms,
            source: source.into(),
            event,
        }
    }

    /// Reject an envelope this build can't speak. Fail-closed: a peer on a
    /// different protocol version yields [`IpcError::VersionMismatch`]
    /// rather than being silently mis-decoded. Called on the read path.
    pub fn require_current(self) -> Result<Self> {
        if self.v == PROTOCOL_VERSION {
            Ok(self)
        } else {
            Err(IpcError::VersionMismatch {
                got: self.v,
                expected: PROTOCOL_VERSION,
            })
        }
    }
}

/// Topic names — mirrored by the Python `events` package.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Topic {
    /// Raw captured PCM frame.
    AudioFrame,
    /// Wake word fired.
    WakeDetected,
    /// VAD detected speech start.
    VadStart,
    /// VAD detected end-of-utterance.
    VadEnd,
    /// Streaming partial ASR transcript.
    AsrPartial,
    /// Final ASR transcript for the utterance.
    AsrFinal,
    /// Routed intent.
    NluIntent,
    /// No fast-path intent matched — route to the conversational brain.
    NluMiss,
    /// The Rust mind asks the Python ML glue to run the model.
    BrainRequest,
    /// LLM streaming reply chunk.
    BrainReply,
    /// The Rust dispatch asks a Python tool to run (weather, news, …).
    ToolRequest,
    /// A Python tool's result.
    ToolResponse,
    /// Streamed TTS audio chunk.
    TtsFrame,
    /// System-level lifecycle event.
    SystemEvent,
    /// Watchdog health / recovery verdict.
    HealthStatus,
    /// Error event.
    Error,
}

impl Topic {
    /// Every topic variant, in declaration order. Used to check the
    /// hand-written enum against the `configs/_schema/events/` schemas.
    pub const ALL: [Topic; 16] = [
        Topic::AudioFrame,
        Topic::WakeDetected,
        Topic::VadStart,
        Topic::VadEnd,
        Topic::AsrPartial,
        Topic::AsrFinal,
        Topic::NluIntent,
        Topic::NluMiss,
        Topic::BrainRequest,
        Topic::BrainReply,
        Topic::ToolRequest,
        Topic::ToolResponse,
        Topic::TtsFrame,
        Topic::SystemEvent,
        Topic::HealthStatus,
        Topic::Error,
    ];

    /// Canonical string form (used as the JSON tag).
    pub fn as_str(self) -> &'static str {
        match self {
            Topic::AudioFrame => "audio.frame",
            Topic::WakeDetected => "wake.detected",
            Topic::VadStart => "vad.start",
            Topic::VadEnd => "vad.end",
            Topic::AsrPartial => "asr.partial",
            Topic::AsrFinal => "asr.final",
            Topic::NluIntent => "nlu.intent",
            Topic::NluMiss => "nlu.miss",
            Topic::BrainRequest => "brain.request",
            Topic::BrainReply => "brain.reply",
            Topic::ToolRequest => "tool.request",
            Topic::ToolResponse => "tool.response",
            Topic::TtsFrame => "tts.frame",
            Topic::SystemEvent => "system.event",
            Topic::HealthStatus => "health.status",
            Topic::Error => "error",
        }
    }
}

/// IPC events tagged by topic.
///
/// Hand-written for M1; generated from JSON Schemas in M2.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "topic", content = "data", rename_all = "snake_case")]
pub enum Event {
    /// Wake-word fired.
    #[serde(rename = "wake.detected")]
    WakeDetected {
        /// Wake model identifier (e.g. `"hey_blazen_pl"`).
        model: String,
        /// Detection probability in `[0, 1]`.
        score: f32,
        /// Spoken-language hint (`"en"` / `"pl"`).
        language: String,
    },

    /// User started speaking.
    #[serde(rename = "vad.start")]
    VadStart,

    /// User stopped speaking.
    #[serde(rename = "vad.end")]
    VadEnd {
        /// Captured speech length in milliseconds.
        duration_ms: u32,
    },

    /// Final ASR transcript for the just-ended utterance.
    #[serde(rename = "asr.final")]
    AsrFinal {
        /// Detected language tag.
        language: String,
        /// Recognised text.
        text: String,
        /// Aggregate confidence in `[0, 1]`.
        confidence: f32,
    },

    /// Fast-path intent match produced by the NLU router (`blazend-nlu`,
    /// over the shared `jessica-core` crate). Action/tool/confirm are not on
    /// the wire — consumers look those up from `configs/intents/system.yaml`.
    #[serde(rename = "nlu.intent")]
    NluIntent {
        /// Matched intent name (e.g. `"volume_up"`).
        intent: String,
        /// Language tag: `"en"` | `"pl"`.
        language: String,
        /// Captured slot params (named regex groups).
        params: std::collections::HashMap<String, String>,
        /// The transcript that matched.
        transcript: String,
    },

    /// No fast-path intent matched the transcript — the conversational brain
    /// should handle it (chat / memory / news). Keeps the brain and the
    /// command dispatcher from both replying to the same utterance.
    #[serde(rename = "nlu.miss")]
    NluMiss {
        /// Language tag: `"en"` | `"pl"`.
        language: String,
        /// The unmatched transcript.
        transcript: String,
    },

    /// The Rust mind (`blazend-mind`) asks the Python ML glue to run the
    /// model. The glue answers with a `brain.reply` carrying the same
    /// `request_id`. The mind builds `prompt` + `system` (persona + injected
    /// context); the glue only runs inference — no routing or memory.
    #[serde(rename = "brain.request")]
    BrainRequest {
        /// Correlates the eventual `brain.reply`.
        request_id: String,
        /// Language tag: `"en"` | `"pl"`.
        language: String,
        /// User prompt (the transcript to answer).
        prompt: String,
        /// System / persona + injected-context prompt.
        #[serde(default)]
        system: Option<String>,
        /// Backend hint (`"local"` | `"ollama"` | `"openai"` | `"gemini"`).
        #[serde(default)]
        backend: Option<String>,
    },

    /// LLM streaming reply token.
    #[serde(rename = "brain.reply")]
    BrainReply {
        /// Token / fragment string.
        chunk: String,
        /// `true` when this is the final chunk in the reply.
        final_: bool,
        /// Reply language tag (`"en"` | `"pl"`) — TTS picks the voice from it.
        #[serde(default)]
        language: Option<String>,
        /// Full reply text (when not streamed chunk-by-chunk).
        #[serde(default)]
        text: Option<String>,
        /// Correlates back to the originating [`Event::BrainRequest`].
        #[serde(default)]
        request_id: Option<String>,
    },

    /// The Rust dispatch asks a Python tool to run (weather, news, web,
    /// radio, semantic recall — API/ML glue). The tool answers with a
    /// `tool.response` carrying the same `request_id`.
    #[serde(rename = "tool.request")]
    ToolRequest {
        /// Correlates the eventual `tool.response`.
        request_id: String,
        /// Tool name (e.g. `"weather.query"`, `"news.brief"`).
        tool: String,
        /// Language tag: `"en"` | `"pl"`.
        language: String,
        /// Tool-specific arguments.
        #[serde(default)]
        args: serde_json::Value,
    },

    /// A Python tool's result — `text` is spoken (the mind re-emits it as a
    /// `brain.reply`); `action`/`payload` carry any side effect (e.g. a radio
    /// stream to start).
    #[serde(rename = "tool.response")]
    ToolResponse {
        /// Correlates back to the originating [`Event::ToolRequest`].
        request_id: String,
        /// Whether the tool succeeded.
        ok: bool,
        /// The spoken reply text.
        text: String,
        /// Reply language tag.
        #[serde(default)]
        language: Option<String>,
        /// Action verb for a side effect (e.g. `"radio"`).
        #[serde(default)]
        action: Option<String>,
        /// Action payload (e.g. `{stream, name}`).
        #[serde(default)]
        payload: Option<serde_json::Value>,
    },

    /// TTS chunk written to audio-out.
    #[serde(rename = "tts.frame")]
    TtsFrame {
        /// Voice used (e.g. `"pl_PL-darkman-medium"`).
        voice: String,
        /// PCM sample count in this chunk.
        samples: u32,
    },

    /// System-level lifecycle event (boot, shutdown, recovery).
    #[serde(rename = "system.event")]
    SystemEvent {
        /// One of: `"ready"`, `"sleep"`, `"resume"`, `"reboot_requested"`, ...
        kind: String,
        /// Optional human-readable detail.
        detail: Option<String>,
    },

    /// Watchdog verdict from `blazend-health`. The orchestrator turns this
    /// into the LED colour + a spoken recovery announcement.
    #[serde(rename = "health.status")]
    HealthStatus {
        /// Severity: `"ok"` | `"degraded"` | `"recovery"` | `"critical"`.
        level: String,
        /// The unit the verdict is about (e.g. `"blazend-brain"`), or `"system"`.
        unit: String,
        /// Human-readable detail.
        detail: String,
        /// Recommended action: `"none"` | `"restart"` | `"recovery_mode"` | `"recovery_image"`.
        action: String,
    },

    /// Error event reported by any unit.
    #[serde(rename = "error")]
    Error {
        /// Short error code (`"asr.no_text"`, `"brain.timeout"`).
        code: String,
        /// Human-readable message.
        message: String,
        /// Optional recovery hint.
        hint: Option<String>,
    },
}

impl Event {
    /// The [`Topic`] this event is published under — the typed counterpart
    /// of the serde `topic` tag written on the wire.
    pub fn topic(&self) -> Topic {
        match self {
            Event::WakeDetected { .. } => Topic::WakeDetected,
            Event::VadStart => Topic::VadStart,
            Event::VadEnd { .. } => Topic::VadEnd,
            Event::AsrFinal { .. } => Topic::AsrFinal,
            Event::NluIntent { .. } => Topic::NluIntent,
            Event::NluMiss { .. } => Topic::NluMiss,
            Event::BrainRequest { .. } => Topic::BrainRequest,
            Event::BrainReply { .. } => Topic::BrainReply,
            Event::ToolRequest { .. } => Topic::ToolRequest,
            Event::ToolResponse { .. } => Topic::ToolResponse,
            Event::TtsFrame { .. } => Topic::TtsFrame,
            Event::SystemEvent { .. } => Topic::SystemEvent,
            Event::HealthStatus { .. } => Topic::HealthStatus,
            Event::Error { .. } => Topic::Error,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn envelope_roundtrip() {
        let env = EventEnvelope::new(
            "blazend-wake",
            12_345,
            Event::WakeDetected {
                model: "hey_blazen_pl".into(),
                score: 0.84,
                language: "pl".into(),
            },
        );
        let json = serde_json::to_string(&env).unwrap();
        assert!(json.contains("\"v\":1"));
        assert!(json.contains("\"topic\":\"wake.detected\""));
        let back: EventEnvelope = serde_json::from_str(&json).unwrap();
        assert_eq!(back.source, "blazend-wake");
    }

    #[test]
    fn topic_strings_are_stable() {
        assert_eq!(Topic::WakeDetected.as_str(), "wake.detected");
        assert_eq!(Topic::AsrFinal.as_str(), "asr.final");
    }

    #[test]
    fn every_topic_has_a_schema() {
        // Tie the hand-written `Topic` enum directly to the schema source of
        // truth: every variant must have a `configs/_schema/events/<topic>.
        // schema.json`. Guards against adding a topic without its schema (and
        // vice-versa) until typed code-gen replaces the enum (M2).
        let schema_dir =
            std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../configs/_schema/events");
        for t in Topic::ALL {
            let schema = schema_dir.join(format!("{}.schema.json", t.as_str()));
            assert!(
                schema.is_file(),
                "no schema for topic {} (expected {})",
                t.as_str(),
                schema.display()
            );
        }
    }

    #[test]
    fn event_topic_matches_the_serde_wire_tag() {
        // For each event, the typed `topic()` must equal the `topic` tag
        // actually serialised — the two hand-maintained mappings can't drift.
        let cases = [
            Event::WakeDetected {
                model: "m".into(),
                score: 0.5,
                language: "pl".into(),
            },
            Event::VadStart,
            Event::VadEnd { duration_ms: 1 },
            Event::AsrFinal {
                language: "pl".into(),
                text: "t".into(),
                confidence: 0.9,
            },
            Event::NluIntent {
                intent: "i".into(),
                language: "pl".into(),
                params: Default::default(),
                transcript: "t".into(),
            },
            Event::NluMiss {
                language: "pl".into(),
                transcript: "t".into(),
            },
            Event::BrainRequest {
                request_id: "r1".into(),
                language: "pl".into(),
                prompt: "p".into(),
                system: None,
                backend: None,
            },
            Event::BrainReply {
                chunk: "c".into(),
                final_: true,
                language: None,
                text: None,
                request_id: None,
            },
            Event::ToolRequest {
                request_id: "t1".into(),
                tool: "weather.query".into(),
                language: "pl".into(),
                args: serde_json::json!({}),
            },
            Event::ToolResponse {
                request_id: "t1".into(),
                ok: true,
                text: "pogoda".into(),
                language: None,
                action: None,
                payload: None,
            },
            Event::TtsFrame {
                voice: "v".into(),
                samples: 1,
            },
            Event::SystemEvent {
                kind: "ready".into(),
                detail: None,
            },
            Event::HealthStatus {
                level: "ok".into(),
                unit: "system".into(),
                detail: "d".into(),
                action: "none".into(),
            },
            Event::Error {
                code: "c".into(),
                message: "m".into(),
                hint: None,
            },
        ];
        for ev in cases {
            let value = serde_json::to_value(&ev).unwrap();
            let tag = value.get("topic").unwrap().as_str().unwrap();
            assert_eq!(tag, ev.topic().as_str(), "topic drift for {ev:?}");
        }
    }

    #[test]
    fn require_current_rejects_a_foreign_protocol_version() {
        let mut env = EventEnvelope::new("u", 1, Event::VadStart);
        assert!(env.clone().require_current().is_ok());
        env.v = PROTOCOL_VERSION + 1;
        let err = env.require_current().unwrap_err();
        assert!(matches!(
            err,
            crate::IpcError::VersionMismatch { got, expected }
                if got == PROTOCOL_VERSION + 1 && expected == PROTOCOL_VERSION
        ));
    }
}
