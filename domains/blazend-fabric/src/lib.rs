//! `blazend-fabric` — multi-device Jessica federation.
//!
//! See `docs/product/11-FABRIC.md` for the design contract.
//!
//! This crate is the Pi-side implementation of the fabric. Mobile gets
//! the equivalent in Dart (`rachel/lib/fabric/`). Both speak the same
//! wire format: framed JSON over TLS-over-TCP, sharing the
//! [`blazend_ipc`] codec.
//!
//! M0 scope (this commit): types, sync log primitives, in-memory CRDT
//! merge, no real networking. Pairing / TLS / mDNS land in M2-M3.

#![deny(unsafe_op_in_unsafe_fn)]
#![warn(missing_docs)]

pub mod fact;
pub mod peer;
pub mod sync_log;

pub use fact::{Fact, FactType};
pub use peer::{PeerId, PeerInfo, PeerKind};
pub use sync_log::{SyncLog, SyncMergeOutcome};

/// Fabric protocol version. Bump on breaking schema changes.
pub const FABRIC_PROTOCOL_VERSION: u32 = 1;

/// Errors raised by the fabric layer.
#[derive(Debug, thiserror::Error)]
pub enum FabricError {
    /// IO error from the underlying transport.
    #[error("io: {0}")]
    Io(#[from] std::io::Error),
    /// JSON encoding / decoding error.
    #[error("json: {0}")]
    Json(#[from] serde_json::Error),
    /// Underlying IPC error.
    #[error("ipc: {0}")]
    Ipc(#[from] blazend_ipc::IpcError),
    /// Unknown peer.
    #[error("unknown peer: {0}")]
    UnknownPeer(String),
    /// Pairing failed (challenge mismatch, expired token, etc.).
    #[error("pairing failed: {0}")]
    PairingFailed(String),
}

/// Result alias for fabric operations.
pub type Result<T> = std::result::Result<T, FabricError>;
