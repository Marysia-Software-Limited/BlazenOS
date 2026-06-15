//! Shared-memory audio ring buffer + a streaming linear resampler — the PCM
//! transport between blazen_os audio units (Rust producers/consumers) and the
//! Python side (`rpi5/src/blazend/audio/`). Metadata/markers travel over the
//! IPC socket; the bulk i16 PCM travels here, out of band.
//!
//! On-disk / in-shm layout (little-endian), mirrored byte-for-byte by the
//! Python `RingReader`/`RingWriter`:
//!
//! ```text
//! off 0  magic:u32 = "BZAR"   off 4  version:u32 = 1
//! off 8  sample_rate_hz:u32    off 12 channels:u32 (=1, mono)
//! off 16 capacity_frames:u32   off 20 _pad:u32
//! off 24 write_pos:u64 (atomic — total frames ever written, monotonic)
//! off 32 samples: i16[capacity_frames]   (ring; index = pos % capacity)
//! ```

use std::fs::OpenOptions;
use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};

use anyhow::{anyhow, Result};
use memmap2::{Mmap, MmapMut};

/// `"BZAR"` as a little-endian u32 — the shm magic.
pub const MAGIC: u32 = u32::from_le_bytes(*b"BZAR");
/// Layout version; bump on any header/format change.
pub const VERSION: u32 = 1;
/// Header size in bytes; PCM samples start here.
pub const HEADER_BYTES: usize = 32;
const WRITE_POS_OFFSET: usize = 24;

fn write_pos_atomic(ptr: *const u8) -> &'static AtomicU64 {
    // The 8 bytes at WRITE_POS_OFFSET are 8-byte aligned (page-aligned base +
    // aligned offset) and only ever accessed through this atomic view.
    unsafe { &*(ptr.add(WRITE_POS_OFFSET) as *const AtomicU64) }
}

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
        write_pos_atomic(mmap.as_ptr()).store(0, Ordering::Release);
        Ok(Self {
            mmap,
            capacity: capacity_frames as usize,
        })
    }

    /// Total frames ever written (monotonic).
    pub fn write_pos(&self) -> u64 {
        write_pos_atomic(self.mmap.as_ptr()).load(Ordering::Acquire)
    }

    /// Append mono i16 frames, advancing `write_pos` by `frames.len()`.
    /// Samples publish (the `Release` store) only after the bytes land.
    pub fn push(&mut self, frames: &[i16]) {
        let cap = self.capacity;
        let mut pos = write_pos_atomic(self.mmap.as_ptr()).load(Ordering::Relaxed) as usize;
        for &sample in frames {
            let idx = HEADER_BYTES + (pos % cap) * 2;
            self.mmap[idx..idx + 2].copy_from_slice(&sample.to_le_bytes());
            pos += 1;
        }
        write_pos_atomic(self.mmap.as_ptr()).store(pos as u64, Ordering::Release);
    }
}

/// Read-only view over a ring created by [`RingWriter`] (or the Python writer).
pub struct RingReader {
    mmap: Mmap,
    pub sample_rate: u32,
    pub channels: u32,
    capacity: usize,
}

impl RingReader {
    /// Open + validate the header of an existing ring file.
    pub fn open(path: impl AsRef<Path>) -> Result<Self> {
        let file = OpenOptions::new().read(true).open(path)?;
        let mmap = unsafe { Mmap::map(&file)? };
        if mmap.len() < HEADER_BYTES {
            return Err(anyhow!("ring file too small"));
        }
        let u32_at = |o: usize| u32::from_le_bytes(mmap[o..o + 4].try_into().unwrap());
        if u32_at(0) != MAGIC {
            return Err(anyhow!("not a blazen audio ring (bad magic)"));
        }
        if u32_at(4) != VERSION {
            return Err(anyhow!("ring version mismatch"));
        }
        let sample_rate = u32_at(8);
        let channels = u32_at(12);
        let capacity = u32_at(16) as usize;
        Ok(Self {
            mmap,
            sample_rate,
            channels,
            capacity,
        })
    }

    /// Total frames ever written (monotonic).
    pub fn write_pos(&self) -> u64 {
        write_pos_atomic(self.mmap.as_ptr()).load(Ordering::Acquire)
    }

    /// Read frames `[start, end)`, clamped to the most recent `capacity` frames
    /// (older data has been overwritten).
    pub fn read_range(&self, start: u64, end: u64) -> Vec<i16> {
        let mut n = end.saturating_sub(start) as usize;
        n = n.min(self.capacity);
        let start = end - n as u64;
        let mut out = Vec::with_capacity(n);
        for i in 0..n {
            let idx = HEADER_BYTES + ((start + i as u64) as usize % self.capacity) * 2;
            out.push(i16::from_le_bytes([self.mmap[idx], self.mmap[idx + 1]]));
        }
        out
    }
}

/// Streaming linear resampler (mono f32). Carries state across buffers.
pub struct LinearResampler {
    step: f64, // input samples advanced per output sample
    pos: f64,
    prev: f32,
    primed: bool,
}

impl LinearResampler {
    pub fn new(in_rate: u32, out_rate: u32) -> Self {
        Self {
            step: in_rate as f64 / out_rate.max(1) as f64,
            pos: 0.0,
            prev: 0.0,
            primed: false,
        }
    }

    /// Append resampled output for `input` (mono f32) to `out`.
    pub fn process(&mut self, input: &[f32], out: &mut Vec<f32>) {
        for &cur in input {
            if !self.primed {
                self.prev = cur;
                self.primed = true;
                continue;
            }
            while self.pos < 1.0 {
                let y = self.prev + (cur - self.prev) * self.pos as f32;
                out.push(y);
                self.pos += self.step;
            }
            self.pos -= 1.0;
            self.prev = cur;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp(name: &str) -> std::path::PathBuf {
        std::env::temp_dir().join(format!("bz-audioring-{name}.shm"))
    }

    #[test]
    fn write_read_roundtrip() {
        let path = tmp("roundtrip");
        {
            let mut w = RingWriter::create(&path, 16_000, 1, 8).unwrap();
            assert_eq!(w.write_pos(), 0);
            w.push(&[1, 2, 3, 4]);
            assert_eq!(w.write_pos(), 4);
        }
        let r = RingReader::open(&path).unwrap();
        assert_eq!((r.sample_rate, r.channels), (16_000, 1));
        assert_eq!(r.write_pos(), 4);
        assert_eq!(r.read_range(0, 4), vec![1, 2, 3, 4]);
        std::fs::remove_file(&path).ok();
    }

    #[test]
    fn wraps_and_clamps_to_capacity() {
        let path = tmp("wrap");
        {
            let mut w = RingWriter::create(&path, 16_000, 1, 4).unwrap();
            w.push(&[10, 20, 30, 40]);
            w.push(&[50, 60]); // overwrites slots 0,1; newest four are 30,40,50,60
            assert_eq!(w.write_pos(), 6);
        }
        let r = RingReader::open(&path).unwrap();
        assert_eq!(r.read_range(2, 6), vec![30, 40, 50, 60]);
        // Asking for more than capacity clamps to the newest `capacity` frames.
        assert_eq!(r.read_range(0, 6), vec![30, 40, 50, 60]);
        std::fs::remove_file(&path).ok();
    }

    #[test]
    fn resampler_identity_and_downsample() {
        // 1:1 passes through.
        let mut r = LinearResampler::new(16_000, 16_000);
        let mut out = Vec::new();
        r.process(&[0.0, 1.0, 2.0, 3.0], &mut out);
        assert!(!out.is_empty());
        // Downsample 2:1 yields about half as many samples.
        let mut r2 = LinearResampler::new(32_000, 16_000);
        let mut out2 = Vec::new();
        let input: Vec<f32> = (0..100).map(|i| i as f32).collect();
        r2.process(&input, &mut out2);
        assert!(out2.len() >= 45 && out2.len() <= 55, "got {}", out2.len());
    }
}
