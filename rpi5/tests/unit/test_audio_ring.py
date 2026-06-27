"""Unit tests for the shared-memory audio ring (`blazend.domains.voice_input.adapters.rpi5.audio`).

Exercises the Python writer/reader pair; the byte layout must match the Rust
`blazend-audio-in` `ring.rs` producer (header magic/version/fields).
"""

from __future__ import annotations

import numpy as np

from blazend.domains.voice_input.adapters.rpi5.audio import (
    HEADER_BYTES,
    MAGIC,
    RingReader,
    RingWriter,
)


def test_header_and_roundtrip(tmp_path):
    path = tmp_path / "ring.shm"
    with RingWriter(path, sample_rate=16_000, channels=1, capacity_frames=8) as w:
        w.push(np.array([1, 2, 3, 4], dtype=np.int16))
        assert w.write_pos == 4

    # header bytes are the cross-language contract
    raw = path.read_bytes()
    assert raw[0:4] == MAGIC
    assert len(raw) == HEADER_BYTES + 8 * 2

    with RingReader(path) as r:
        assert (r.sample_rate, r.channels, r.capacity) == (16_000, 1, 8)
        assert r.write_pos == 4
        assert list(r.read_range(0, 4)) == [1, 2, 3, 4]


def test_wraparound_keeps_newest_frames():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "ring.shm"
        with RingWriter(path, capacity_frames=4) as w:
            w.push(np.array([10, 20, 30, 40], dtype=np.int16))  # fills
            w.push(np.array([50, 60], dtype=np.int16))  # overwrites slots 0,1
            assert w.write_pos == 6
        with RingReader(path) as r:
            # frames [2,6) = the four most recent, in order
            assert list(r.read_range(2, 6)) == [30, 40, 50, 60]


def test_read_clamped_to_capacity(tmp_path):
    path = tmp_path / "ring.shm"
    with RingWriter(path, capacity_frames=4) as w:
        w.push(np.arange(10, dtype=np.int16))  # 0..9, wraps
    with RingReader(path) as r:
        out = r.read_range(0, 10)  # asks 10, only 4 survive
        assert len(out) == 4
        assert list(out) == [6, 7, 8, 9]


def test_empty_read():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "ring.shm"
        with RingWriter(path, capacity_frames=4):
            pass
        with RingReader(path) as r:
            assert len(r.read_range(0, 0)) == 0
