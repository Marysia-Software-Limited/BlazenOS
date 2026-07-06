//! The output boundary. The portable engine decodes + processes audio and hands
//! interleaved `i16` frames to an [`AudioSink`]; each platform provides its own
//! sink (ALSA on the Pi, cpal on macOS). A [`SinkFactory`] opens a sink once the
//! engine knows the stream's sample rate + channel count.

use anyhow::Result;

/// A place to write interleaved `i16` audio frames. `write` should block/pace so
/// the engine's decode stays ahead of real-time without unbounded buffering.
///
/// Used single-threaded by the engine (created and driven on `play_file`'s
/// thread), so no `Send` bound — a cpal sink may hold a `!Send` stream.
pub trait AudioSink {
    fn write(&mut self, interleaved: &[i16]) -> Result<()>;
    /// Block until buffered audio has finished playing (called at end of file).
    fn drain(&mut self) {}
}

/// Opens an [`AudioSink`] configured for a given sample rate + channel count.
pub trait SinkFactory {
    fn open(&self, rate: u32, channels: usize) -> Result<Box<dyn AudioSink>>;
}

/// A sink that counts frames and keeps the last chunk — for tests without a device.
#[derive(Default)]
pub struct CountingSink {
    pub frames_written: usize,
    pub drained: bool,
}

impl AudioSink for CountingSink {
    fn write(&mut self, interleaved: &[i16]) -> Result<()> {
        self.frames_written += interleaved.len();
        Ok(())
    }
    fn drain(&mut self) {
        self.drained = true;
    }
}
