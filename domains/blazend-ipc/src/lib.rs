//! `blazend-ipc` — shared IPC for every blazend-* Rust unit.
//!
//! See `docs/01-ARCHITECTURE.md` and `docs/14-RUST-PYTHON-SPLIT.md`.
//!
//! Wire format: length-prefixed framed JSON on Unix-domain sockets under
//! `/run/blazen/`. Each message is `{ "v": 1, "topic": "...", "ts": ..., "data": {...} }`.

#![deny(unsafe_op_in_unsafe_fn)]
#![warn(missing_docs)]

pub mod codec;
pub mod events;
pub mod socket;

pub use codec::{Frame, FrameCodec};
pub use events::{Event, EventEnvelope, Topic};
pub use socket::{runtime_dir, Publisher, Subscriber};

/// IPC protocol version. Bump on breaking schema changes.
pub const PROTOCOL_VERSION: u32 = 1;

/// Errors raised by the IPC layer.
#[derive(Debug, thiserror::Error)]
pub enum IpcError {
    /// IO error from the underlying socket.
    #[error("io: {0}")]
    Io(#[from] std::io::Error),
    /// JSON encoding / decoding error.
    #[error("json: {0}")]
    Json(#[from] serde_json::Error),
    /// Frame exceeded the negotiated maximum size.
    #[error("frame too large: {0} bytes")]
    FrameTooLarge(usize),
    /// Peer sent an unknown topic.
    #[error("unknown topic: {0}")]
    UnknownTopic(String),
    /// Protocol version mismatch.
    #[error("protocol version mismatch: got {got}, expected {expected}")]
    VersionMismatch {
        /// Version received on the wire.
        got: u32,
        /// Version this build of `blazend-ipc` understands.
        expected: u32,
    },
}

/// Result alias for IPC operations.
pub type Result<T> = std::result::Result<T, IpcError>;
