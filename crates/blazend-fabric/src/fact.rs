//! Sync-log facts — the canonical units of fabric state.

use serde::{Deserialize, Serialize};

/// Catalogue of fact types the M1 fabric understands.
///
/// See `docs/product/11-FABRIC.md` §5 — the implementations must agree
/// on every variant. Adding a variant is a backwards-compatible change
/// (older peers ignore it); removing one bumps the protocol version.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FactType {
    /// User-facing name + Polish vocative.
    #[serde(rename = "identity.user_name")]
    IdentityUserName,
    /// Per-user voice embedding (256-d).
    #[serde(rename = "voice_id.embedding")]
    VoiceIdEmbedding,
    /// Wake-word per-user threshold.
    #[serde(rename = "wake.threshold_per_user")]
    WakeThresholdPerUser,
    /// A retrained wake-word model blob descriptor (binary fetched via RPC).
    #[serde(rename = "wake.model_blob")]
    WakeModelBlob,
    /// A new voice note.
    #[serde(rename = "note.created")]
    NoteCreated,
    /// A note deletion.
    #[serde(rename = "note.deleted")]
    NoteDeleted,
    /// A new reminder.
    #[serde(rename = "reminder.created")]
    ReminderCreated,
    /// A reminder marked done.
    #[serde(rename = "reminder.acked")]
    ReminderAcked,
    /// A friend added to the curated FB list.
    #[serde(rename = "curated_friend.added")]
    CuratedFriendAdded,
    /// A voice-policy mutation.
    #[serde(rename = "voice_policy.updated")]
    VoicePolicyUpdated,
    /// A cloud-policy mutation.
    #[serde(rename = "cloud_policy.updated")]
    CloudPolicyUpdated,
    /// A briefing config mutation.
    #[serde(rename = "briefing.config_updated")]
    BriefingConfigUpdated,
    /// Mute list updated.
    #[serde(rename = "mute_list.updated")]
    MuteListUpdated,
    /// Conversation summary closed.
    #[serde(rename = "conversation.summary")]
    ConversationSummary,
    /// A peer joined the fabric.
    #[serde(rename = "fabric.peer_introduced")]
    FabricPeerIntroduced,
    /// A peer removed from the fabric.
    #[serde(rename = "fabric.peer_removed")]
    FabricPeerRemoved,
}

/// A single fact in the sync log.
///
/// Stored append-only on each peer; conflict resolution at fact-id level.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Fact {
    /// Protocol version.
    pub v: u32,
    /// ULID — globally unique, sortable.
    pub fact_id: String,
    /// Type tag.
    pub fact_type: FactType,
    /// Monotonic-since-fabric-epoch milliseconds.
    pub ts_ms: u64,
    /// Originating peer id (slug).
    pub origin: String,
    /// Vector clock — one slot per known peer; resolves causality.
    #[serde(default)]
    pub lamport: Vec<u32>,
    /// Fact-specific payload.
    pub payload: serde_json::Value,
    /// Ed25519 signature of `(fact_id || fact_type || ts_ms || origin || payload)`.
    #[serde(default)]
    pub signature: String,
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn fact_roundtrip() {
        let f = Fact {
            v: 1,
            fact_id: "01HZRG8K2N0".into(),
            fact_type: FactType::NoteCreated,
            ts_ms: 1_718_093_132_000,
            origin: "pi-kitchen".into(),
            lamport: vec![4, 17, 0, 0],
            payload: json!({"note_id": "n-1", "body": "kup mleko", "lang": "pl"}),
            signature: "".into(),
        };
        let s = serde_json::to_string(&f).unwrap();
        let back: Fact = serde_json::from_str(&s).unwrap();
        assert_eq!(back.fact_type, FactType::NoteCreated);
        assert_eq!(back.origin, "pi-kitchen");
    }
}
