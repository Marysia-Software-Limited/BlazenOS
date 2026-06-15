//! Shared-memory audio ring buffer — the PCM transport between the Rust
//! capture unit (single producer) and the Python ASR/VAD consumers
//! (`rpi5/src/blazend/audio/`). Metadata/markers travel over the IPC socket
//! (`vad.start` / `vad.end`); the bulk i16 PCM travels here, out of band.
//!
//! On-disk / in-shm layout (little-endian), mirrored byte-for-byte by the
//! Python `RingReader`:
//!
//! ```text
//! off 0  magic:u32 = "BZAR"   off 4  version:u32 = 1
//! off 8  sample_rate_hz:u32    off 12 channels:u32 (=1, mono)
//! off 16 capacity_frames:u32   off 20 _pad:u32
//! off 24 write_pos:u64 (atomic — total frames ever written, monotonic)
//! off 32 samples: i16[capacity_frames]   (ring; index = pos % capacity)
//! ```
//!
//! `write_pos` is monotonic; a reader takes a live window
//! `[write_pos - pre_roll .. write_pos]` and indexes modulo `capacity_frames`.

use std::fs::OpenOptions;
use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};

use anyhow::Result;
use memmap2::MmapMut;

/// `"BZAR"` as a little-endian u32 — the shm magic.
pub const MAGIC: u32 = u32::from_le_bytes(*b"BZAR");
/// Layout version; bump on any header/format change.
pub const VERSION: u32 = 1;
/// Header size in bytes; PCM samples start here.
pub const HEADER_BYTES: usize = 32;
const WRITE_POS_OFFSET: usize = 24;

/// Single-producer writer over a memory-mapped ring file.
pub struct RingWriter {
    mmap: MmapMut,
    capacity: usize,
}

impl RingWriter {
    /// Create (or re-create) the ring file and write a fresh header.
    pub fn create(
        path: impl AsRef<Path>,
        sample_rate_hz: u32,
        channels: u32,
        capacity_frames: u32,
    ) -> Result<Self> {
        let total = HEADER_BYTES + capacity_frames as usize * 2;
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(path)?;
        file.set_len(total as u64)?;
        let mut mmap = unsafe { MmapMut::map_mut(&file)? };
        mmap[0..4].copy_from_slice(&MAGIC.to_le_bytes());
        mmap[4..8].copy_from_slice(&VERSION.to_le_bytes());
        mmap[8..12].copy_from_slice(&sample_rate_hz.to_le_bytes());
        mmap[12..16].copy_from_slice(&channels.to_le_bytes());
        mmap[16..20].copy_from_slice(&capacity_frames.to_le_bytes());
        mmap[20..24].copy_from_slice(&0u32.to_le_bytes());
        let writer = Self {
            mmap,
            capacity: capacity_frames as usize,
        };
        writer.write_pos_atomic().store(0, Ordering::Release);
        Ok(writer)
    }

    fn write_pos_atomic(&self) -> &AtomicU64 {
        // The 8 bytes at WRITE_POS_OFFSET are 8-byte aligned (page-aligned base
        // + aligned offset) and only ever accessed through this atomic view.
        unsafe { &*(self.mmap.as_ptr().add(WRITE_POS_OFFSET) as *const AtomicU64) }
    }

    /// Total frames ever written (monotonic).
    pub fn write_pos(&self) -> u64 {
        self.write_pos_atomic().load(Ordering::Acquire)
    }

    /// Append mono i16 frames, advancing `write_pos` by `frames.len()`.
    /// Samples publish (the `Release` store) only after the bytes land.
    pub fn push(&mut self, frames: &[i16]) {
        let cap = self.capacity;
        let mut pos = self.write_pos_atomic().load(Ordering::Relaxed) as usize;
        for &sample in frames {
            let idx = HEADER_BYTES + (pos % cap) * 2;
            self.mmap[idx..idx + 2].copy_from_slice(&sample.to_le_bytes());
            pos += 1;
        }
        self.write_pos_atomic().store(pos as u64, Ordering::Release);
    }

    #[cfg(test)]
    fn frame_at(&self, pos: usize) -> i16 {
        let idx = HEADER_BYTES + (pos % self.capacity) * 2;
        i16::from_le_bytes([self.mmap[idx], self.mmap[idx + 1]])
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp(name: &str) -> std::path::PathBuf {
        std::env::temp_dir().join(format!("bz-ringtest-{name}.shm"))
    }

    #[test]
    fn header_and_roundtrip() {
        let path = tmp("roundtrip");
        let mut w = RingWriter::create(&path, 16_000, 1, 8).unwrap();
        assert_eq!(w.write_pos(), 0);
        w.push(&[1, 2, 3, 4]);
        assert_eq!(w.write_pos(), 4);
        for (i, expect) in [1, 2, 3, 4].iter().enumerate() {
            assert_eq!(w.frame_at(i), *expect);
        }
        // header round-trips
        assert_eq!(MAGIC, u32::from_le_bytes(*b"BZAR"));
        std::fs::remove_file(&path).ok();
    }

    #[test]
    fn wraps_around_and_keeps_write_pos_monotonic() {
        let path = tmp("wrap");
        let mut w = RingWriter::create(&path, 16_000, 1, 4).unwrap();
        w.push(&[10, 20, 30, 40]); // fills exactly
        w.push(&[50, 60]); // overwrites slots 0,1
        assert_eq!(w.write_pos(), 6);
        // newest two frames sit where 10,20 were
        assert_eq!(w.frame_at(4), 50);
        assert_eq!(w.frame_at(5), 60);
        // slot 0/1 now hold the wrapped values; 2,3 still original
        assert_eq!(w.frame_at(2), 30);
        assert_eq!(w.frame_at(3), 40);
        std::fs::remove_file(&path).ok();
    }
}
