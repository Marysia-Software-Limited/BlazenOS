//! Arg parsing for `rachel-player`, split into a lib so it's unit-testable.
//!
//! Flags mirror the Pi appliance's `blazend-player` (the audiobook-relevant
//! subset) so the two players behave identically: resume via `--start-seconds`,
//! progress via `--position-file`, and the same dynamics knobs.

use std::path::PathBuf;

use blazend_audiobook::{DynamicsConfig, FileConfig};
use clap::Parser;

#[derive(Parser, Debug)]
#[command(
    name = "rachel-player",
    about = "Play one audiobook chapter (macOS/cpal)."
)]
pub struct Args {
    /// Audio file to play (one chapter).
    pub source: PathBuf,
    /// Resume: seek this many seconds into the file before playing.
    #[arg(long, default_value_t = 0.0)]
    pub start_seconds: f64,
    /// Write playback position ({"seconds",done}) here for resume / auto-advance.
    #[arg(long)]
    pub position_file: Option<PathBuf>,
    /// Prebuffer before the first output write.
    #[arg(long, default_value_t = 200)]
    pub prebuffer_ms: u32,
    /// Manual pre-gain (linear), applied before the leveler.
    #[arg(long, default_value_t = 1.0)]
    pub gain: f32,
    /// Disable the always-on loudness leveler.
    #[arg(long = "no-level", default_value_t = false)]
    pub no_level: bool,
    /// Enable the speech compressor (books/podcasts).
    #[arg(long, default_value_t = false)]
    pub compress: bool,
    #[arg(long, default_value_t = -16.0)]
    pub target_db: f32,
    #[arg(long, default_value_t = 20.0)]
    pub max_boost_db: f32,
    #[arg(long, default_value_t = -1.0)]
    pub limit_db: f32,
    #[arg(long, default_value_t = -24.0)]
    pub comp_threshold_db: f32,
    #[arg(long, default_value_t = 3.0)]
    pub comp_ratio: f32,
    #[arg(long, default_value_t = 3.0)]
    pub comp_makeup_db: f32,
}

impl Args {
    pub fn into_config(self) -> FileConfig {
        FileConfig {
            source: self.source,
            start_seconds: self.start_seconds,
            position_file: self.position_file,
            prebuffer_ms: self.prebuffer_ms,
            dynamics: DynamicsConfig {
                pre_gain: self.gain,
                level: !self.no_level,
                target_db: self.target_db,
                max_boost_db: self.max_boost_db,
                compress: self.compress,
                comp_threshold_db: self.comp_threshold_db,
                comp_ratio: self.comp_ratio,
                comp_makeup_db: self.comp_makeup_db,
                limit_db: self.limit_db,
            },
        }
    }
}

/// Parse an argv vector into a ready [`FileConfig`]. Errors on bad flags.
pub fn parse<I, T>(argv: I) -> anyhow::Result<FileConfig>
where
    I: IntoIterator<Item = T>,
    T: Into<std::ffi::OsString> + Clone,
{
    Ok(Args::try_parse_from(argv)?.into_config())
}
