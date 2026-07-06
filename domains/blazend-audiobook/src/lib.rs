//! Portable audiobook playback engine for blazen_os.
//!
//! Decodes an audio file (symphonia), applies the shared loudness/compression
//! dynamics chain, and streams interleaved `i16` frames to a platform
//! [`AudioSink`] — ALSA on the Pi appliance, cpal on the rachel macOS agent.
//! Plays ONE file; chapter auto-advance is the orchestrator's job, matching the
//! appliance's `blazend-player`. Lives under `domains/` so every node links one
//! engine instead of copying it.

mod dynamics;
mod engine;
mod sink;

pub use dynamics::{db_to_lin, Dynamics, DynamicsCfg, DynamicsConfig};
pub use engine::{play_file, write_position, FileConfig};
pub use sink::{AudioSink, CountingSink, SinkFactory};
