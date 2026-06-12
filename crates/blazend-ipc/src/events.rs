//! IPC event types.
//!
//! Authoritative schemas live under `configs/_schema/events/*.schema.json`
//! and (post-M1) generate `_generated.rs` next to this file. Until the
//! generator is wired in, these hand-written types are the contract;
//! the schema CI gate will be added in M2.

use serde::{Deserialize, Serialize};

use crate::PROTOCOL_VERSION;

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
    /// LLM streaming reply chunk.
    BrainReply,
    /// Streamed TTS audio chunk.
    TtsFrame,
    /// System-level lifecycle event.
    SystemEvent,
    /// Error event.
    Error,
}

impl Topic {
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
            Topic::BrainReply => "brain.reply",
            Topic::TtsFrame => "tts.frame",
            Topic::SystemEvent => "system.event",
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

    /// LLM streaming reply token.
    #[serde(rename = "brain.reply")]
    BrainReply {
        /// Token / fragment string.
        chunk: String,
        /// `true` when this is the final chunk in the reply.
        final_: bool,
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
}
