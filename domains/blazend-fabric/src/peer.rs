//! Peers — devices participating in this fabric.

use serde::{Deserialize, Serialize};

/// Stable ID for a peer. Slug-style; assigned at pairing time and
/// signed by the peer's identity key.
#[derive(Debug, Clone, Hash, PartialEq, Eq, Serialize, Deserialize)]
pub struct PeerId(pub String);

impl PeerId {
    /// Build a peer id from any string-like.
    pub fn new(s: impl Into<String>) -> Self {
        Self(s.into())
    }

    /// Borrow as `&str`.
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

/// What kind of device the peer is. Drives default offered capabilities.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PeerKind {
    /// Raspberry Pi appliance (this crate runs on one of these).
    Appliance,
    /// iOS phone.
    #[serde(rename = "mobile.ios")]
    MobileIos,
    /// Android phone.
    #[serde(rename = "mobile.android")]
    MobileAndroid,
    /// iPad.
    #[serde(rename = "tablet.ios")]
    TabletIos,
    /// Android tablet.
    #[serde(rename = "tablet.android")]
    TabletAndroid,
    /// watchOS wearable (M11+).
    #[serde(rename = "wearable.watchos")]
    WearableWatchos,
    /// Wear OS wearable (M11+).
    #[serde(rename = "wearable.wearos")]
    WearableWearos,
}

/// Static info about a peer.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PeerInfo {
    /// Stable peer id.
    pub id: PeerId,
    /// Human-readable name (`"iPhone w kieszeni"`).
    pub name: String,
    /// Device kind.
    pub kind: PeerKind,
    /// Currently offered capabilities (e.g. `["llm.cpu", "tts.piper"]`).
    pub capabilities: Vec<String>,
    /// Last time we saw this peer alive, in monotonic milliseconds.
    #[serde(default)]
    pub last_seen_ms: u64,
}

impl PeerInfo {
    /// Whether this peer offers the named capability.
    pub fn offers(&self, capability: &str) -> bool {
        self.capabilities.iter().any(|c| c == capability)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn capability_lookup() {
        let p = PeerInfo {
            id: PeerId::new("pi-kitchen"),
            name: "Pi w kuchni".into(),
            kind: PeerKind::Appliance,
            capabilities: vec!["llm.cpu".into(), "tts.piper".into(), "speaker.room".into()],
            last_seen_ms: 0,
        };
        assert!(p.offers("llm.cpu"));
        assert!(!p.offers("gps"));
    }

    #[test]
    fn peer_kind_roundtrip() {
        let k = PeerKind::MobileIos;
        let s = serde_json::to_string(&k).unwrap();
        assert_eq!(s, "\"mobile.ios\"");
        let back: PeerKind = serde_json::from_str(&s).unwrap();
        assert_eq!(back, PeerKind::MobileIos);
    }
}
