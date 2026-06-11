//! `jessica-mobile-core` — shared mobile business logic.
//!
//! Pure Rust, no platform deps. Reused via FFI by `jessica-ios` (Swift
//! Package) and `jessica-android` (AAR through JNI).
//!
//! See `blazen_os/docs/product/15-NATIVE-MIGRATION.md` for the role
//! this crate plays in the iOS + Android shipping mobile apps.
//!
//! M0 (this commit): types + intent router skeleton ported from
//! `rachel/lib/intents/router.dart`. Sync log reuses
//! `blazend-fabric`. M1 fills in conversation memory, notes,
//! reminders, profile DB (SQLite via `rusqlite`).

#![deny(unsafe_op_in_unsafe_fn)]
#![warn(missing_docs)]

pub mod intent;

// Re-export the fabric types so mobile consumers only need to depend
// on this crate, not blazend-fabric directly.
pub use blazend_fabric::{Fact, FactType, PeerInfo, PeerKind, SyncLog, SyncMergeOutcome};
pub use intent::{IntentMatch, IntentRouter};

/// Crate-wide error type.
#[derive(Debug, thiserror::Error)]
pub enum CoreError {
    /// Underlying fabric error.
    #[error("fabric: {0}")]
    Fabric(#[from] blazend_fabric::FabricError),
    /// YAML deserialisation error.
    #[error("yaml: {0}")]
    Yaml(#[from] serde_yaml::Error),
    /// Regex compilation error.
    #[error("regex: {0}")]
    Regex(#[from] regex::Error),
    /// Generic config error.
    #[error("config: {0}")]
    Config(String),
}

/// Result alias.
pub type Result<T> = std::result::Result<T, CoreError>;
