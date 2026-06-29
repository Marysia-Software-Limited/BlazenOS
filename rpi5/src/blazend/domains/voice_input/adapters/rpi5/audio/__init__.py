"""Shared-memory audio ring buffer — Python side of the Rust `blazend-audio-in`
contract (`rpi5/voice-input/blazend-audio-in/src/ring.rs`).

The Rust capture unit is the single producer; `blazend-asr` (and, later, other
consumers) read the i16 mono PCM here while `vad.start` / `vad.end` markers
arrive over the IPC socket. `RingWriter` is only for tests + diagnostic tools
(in production the Rust unit owns writing); `RingReader` is what the appliance
uses.

Header layout (little-endian), identical to the Rust side::

    off 0  magic:4s = b"BZAR"   off 4  version:u32
    off 8  sample_rate_hz:u32   off 12 channels:u32
    off 16 capacity_frames:u32  off 20 _pad:u32
    off 24 write_pos:u64        off 32 samples: i16[capacity_frames]
"""

from __future__ import annotations

import mmap
import struct
from pathlib import Path
from types import TracebackType

import numpy as np
import numpy.typing as npt

MAGIC = b"BZAR"
VERSION = 1
HEADER_BYTES = 32
_WRITE_POS_OFFSET = 24


class RingReader:
    """Read-only view over the shared-memory audio ring."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._file = open(self.path, "rb")  # noqa: SIM115 (held for mmap lifetime)
        self._mm = mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ)
        if bytes(self._mm[0:4]) != MAGIC:
            raise ValueError(f"{self.path}: not a blazen audio ring (bad magic)")
        version, self.sample_rate, self.channels, self.capacity = struct.unpack_from(
            "<IIII", self._mm, 4
        )
        if version != VERSION:
            raise ValueError(f"{self.path}: ring version {version} != {VERSION}")

    @property
    def write_pos(self) -> int:
        """Total frames ever written (monotonic)."""
        return int(struct.unpack_from("<Q", self._mm, _WRITE_POS_OFFSET)[0])

    def read_range(self, start: int, end: int) -> npt.NDArray[np.int16]:
        """Return frames ``[start, end)`` as int16, clamped to the most recent
        ``capacity`` frames (older data has been overwritten)."""
        n = max(0, end - start)
        n = min(n, self.capacity)
        start = end - n
        if n == 0:
            return np.empty(0, dtype=np.int16)
        samples: npt.NDArray[np.int16] = np.frombuffer(
            self._mm, dtype="<i2", count=self.capacity, offset=HEADER_BYTES
        )
        idx = np.arange(start, end) % self.capacity
        out: npt.NDArray[np.int16] = np.ascontiguousarray(samples[idx], dtype=np.int16)
        return out

    def close(self) -> None:
        self._mm.close()
        self._file.close()

    def __enter__(self) -> RingReader:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


class RingWriter:
    """Create + write a ring file. Test/diagnostic use only — the appliance's
    producer is the Rust `blazend-audio-in` unit."""

    def __init__(
        self,
        path: str | Path,
        sample_rate: int = 16_000,
        channels: int = 1,
        capacity_frames: int = 48_000,
    ) -> None:
        self.path = Path(path)
        self.capacity = capacity_frames
        total = HEADER_BYTES + capacity_frames * 2
        with open(self.path, "wb") as f:
            f.truncate(total)
        self._file = open(self.path, "r+b")  # noqa: SIM115 (held for mmap lifetime)
        self._mm = mmap.mmap(self._file.fileno(), 0)
        struct.pack_into(
            "<4sIIIIIQ", self._mm, 0, MAGIC, VERSION, sample_rate, channels, capacity_frames, 0, 0
        )

    @property
    def write_pos(self) -> int:
        return int(struct.unpack_from("<Q", self._mm, _WRITE_POS_OFFSET)[0])

    def push(self, samples: npt.NDArray[np.int16]) -> None:
        """Append mono int16 frames, advancing write_pos."""
        data = np.asarray(samples, dtype="<i2")
        pos = self.write_pos
        n = len(data)
        ring: npt.NDArray[np.int16] = np.ndarray(
            (self.capacity,), dtype="<i2", buffer=self._mm, offset=HEADER_BYTES
        )
        idx = np.arange(pos, pos + n) % self.capacity
        ring[idx] = data
        struct.pack_into("<Q", self._mm, _WRITE_POS_OFFSET, pos + n)

    def close(self) -> None:
        self._mm.flush()
        self._mm.close()
        self._file.close()

    def __enter__(self) -> RingWriter:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
