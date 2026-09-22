"""Tier 0 — intercom call leg (2026-09-22): the ring→stream pump."""
from __future__ import annotations

import io
import threading

import numpy as np

from blazend.domains.voice_input.adapters.rpi5.audio import RingReader, RingWriter
from blazend.domains.voice_input.adapters.rpi5.intercom import pump_ring


def test_pump_ring_copies_new_frames_and_respects_the_cap(tmp_path):
    path = tmp_path / "audio-ring.shm"
    w = RingWriter(path, sample_rate=16_000)
    pcm = (np.arange(16_000, dtype=np.int16) % 2000)
    w.push(pcm[:8000])  # pre-call audio — must NOT be sent
    reader = RingReader(path)
    out = io.BytesIO()
    stop = threading.Event()

    feeder = threading.Thread(
        target=lambda: (w.push(pcm[8000:]),), daemon=True)
    feeder.start()
    pump_ring(reader, out, stop, seconds=0.5)

    sent = np.frombuffer(out.getvalue(), dtype="<i2")
    assert len(sent) == 8000                      # only frames written DURING the call
    assert np.array_equal(sent, pcm[8000:])
    assert stop.is_set()                          # cap reached → leg signals hang-up
    reader.close()
    w.close()


def test_pump_ring_stops_on_broken_pipe(tmp_path):
    path = tmp_path / "audio-ring.shm"
    w = RingWriter(path, sample_rate=16_000)
    reader = RingReader(path)

    class _Broken(io.RawIOBase):
        def write(self, _b):  # peer hung up
            raise BrokenPipeError

    stop = threading.Event()
    w.push(np.ones(4000, dtype=np.int16))
    pump_ring(reader, _Broken(), stop, seconds=5.0)  # returns fast, not after 5 s
    assert stop.is_set()
    reader.close()
    w.close()
