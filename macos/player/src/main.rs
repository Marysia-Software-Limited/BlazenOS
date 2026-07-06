//! rachel-player — plays one audiobook chapter on macOS through the shared
//! `blazend-audiobook` engine + a cpal (CoreAudio) sink. Chapter looping /
//! auto-advance is the Python agent's job, matching the Pi's `blazend-player`.

use anyhow::Result;
use blazend_audiobook::{play_file, CpalSinkFactory};

fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "info,symphonia=error".into()),
        )
        .init();
    let cfg = rachel_player::parse(std::env::args_os())?;
    play_file(&CpalSinkFactory, &cfg)
}
