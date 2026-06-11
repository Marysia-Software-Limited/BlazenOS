"""Async Unix-socket pub/sub matching the Rust `blazend-ipc` wire format.

Wire format: 4-byte big-endian length prefix + UTF-8 JSON payload.
"""

from __future__ import annotations

import asyncio
import json
import os
import struct
from pathlib import Path

from blazend.events import Envelope

MAX_FRAME_BYTES = 1 << 20


def runtime_dir() -> Path:
    """Pick the runtime directory matching the Rust side.

    Order: ``BLAZEN_RUNTIME_DIR`` → ``$XDG_RUNTIME_DIR/blazen`` →
    ``/tmp/blazen-<uid>``.
    """
    if (override := os.environ.get("BLAZEN_RUNTIME_DIR")) is not None:
        return Path(override)
    if (xdg := os.environ.get("XDG_RUNTIME_DIR")) is not None:
        return Path(xdg) / "blazen"
    return Path(f"/tmp/blazen-{os.getuid()}")


async def _read_frame(reader: asyncio.StreamReader) -> dict | None:
    header = await reader.readexactly(4) if not reader.at_eof() else b""
    if not header:
        return None
    (length,) = struct.unpack(">I", header)
    if length == 0:
        return None
    if length > MAX_FRAME_BYTES:
        raise ValueError(f"frame too large: {length} bytes")
    payload = await reader.readexactly(length)
    return json.loads(payload.decode("utf-8"))


async def _write_frame(writer: asyncio.StreamWriter, obj: dict) -> None:
    payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    if len(payload) > MAX_FRAME_BYTES:
        raise ValueError(f"frame too large: {len(payload)} bytes")
    writer.write(struct.pack(">I", len(payload)))
    writer.write(payload)
    await writer.drain()


class Subscriber:
    """Connects to a publisher socket and yields envelopes."""

    def __init__(self, socket_path: Path):
        self._socket_path = socket_path
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def connect(self) -> None:
        """Open the connection."""
        self._reader, self._writer = await asyncio.open_unix_connection(str(self._socket_path))

    def __aiter__(self) -> "Subscriber":
        return self

    async def __anext__(self) -> Envelope:
        assert self._reader is not None, "call .connect() first"
        try:
            raw = await _read_frame(self._reader)
        except asyncio.IncompleteReadError:
            raise StopAsyncIteration from None
        if raw is None:
            raise StopAsyncIteration
        return Envelope.from_wire(raw)

    async def next(self) -> Envelope | None:
        """Read the next envelope; ``None`` on EOF."""
        try:
            return await self.__anext__()
        except StopAsyncIteration:
            return None

    async def close(self) -> None:
        """Close the underlying writer."""
        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass


class Publisher:
    """Binds a Unix socket and broadcasts envelopes to every connected peer."""

    def __init__(self, socket_path: Path):
        self._socket_path = socket_path
        self._server: asyncio.base_events.Server | None = None
        self._writers: list[asyncio.StreamWriter] = []
        self._closing = asyncio.Event()

    async def bind(self) -> None:
        """Bind the socket and start accepting subscribers."""
        self._socket_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._socket_path.unlink()
        except FileNotFoundError:
            pass
        self._server = await asyncio.start_unix_server(self._on_connect, path=str(self._socket_path))

    async def _on_connect(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        # In asyncio.start_unix_server the handler must keep running for
        # the connection to stay open. We track the writer and park until
        # either the peer disconnects (reader EOF) or we're closing.
        self._writers.append(writer)
        try:
            done = asyncio.create_task(self._closing.wait())
            eof = asyncio.create_task(reader.read())
            await asyncio.wait({done, eof}, return_when=asyncio.FIRST_COMPLETED)
            for t in (done, eof):
                if not t.done():
                    t.cancel()
        finally:
            if writer in self._writers:
                self._writers.remove(writer)
            try:
                writer.close()
            except Exception:  # noqa: BLE001
                pass

    async def publish(self, envelope: Envelope) -> None:
        """Broadcast an envelope to every connected subscriber."""
        if not self._writers:
            return
        wire = envelope.to_wire()
        dead: list[asyncio.StreamWriter] = []
        for w in list(self._writers):
            try:
                await _write_frame(w, wire)
            except (ConnectionResetError, BrokenPipeError, OSError):
                dead.append(w)
        for w in dead:
            if w in self._writers:
                self._writers.remove(w)
            w.close()

    async def close(self) -> None:
        """Stop accepting new subscribers and close existing ones."""
        self._closing.set()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        for w in list(self._writers):
            w.close()
        self._writers.clear()


__all__ = ["Publisher", "Subscriber", "runtime_dir", "MAX_FRAME_BYTES"]
