"""IPC event envelope + topic constants.

Mirrors `crates/blazend-ipc/src/events.rs`. The schema-driven generator
in `scripts/gen-event-types.py` overwrites `_generated.py` from
`configs/_schema/events/*.schema.json`; until M2 we hand-maintain the
parallel definitions.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

PROTOCOL_VERSION = 1


# Topic strings — kept in sync with Topic::as_str() in the Rust crate.
TOPIC_AUDIO_FRAME = "audio.frame"
TOPIC_WAKE_DETECTED = "wake.detected"
TOPIC_VAD_START = "vad.start"
TOPIC_VAD_END = "vad.end"
TOPIC_ASR_PARTIAL = "asr.partial"
TOPIC_ASR_FINAL = "asr.final"
TOPIC_NLU_INTENT = "nlu.intent"
TOPIC_BRAIN_REPLY = "brain.reply"
TOPIC_TTS_FRAME = "tts.frame"
TOPIC_SYSTEM_EVENT = "system.event"
TOPIC_ERROR = "error"

ALL_TOPICS: tuple[str, ...] = (
    TOPIC_AUDIO_FRAME, TOPIC_WAKE_DETECTED,
    TOPIC_VAD_START, TOPIC_VAD_END,
    TOPIC_ASR_PARTIAL, TOPIC_ASR_FINAL,
    TOPIC_NLU_INTENT, TOPIC_BRAIN_REPLY,
    TOPIC_TTS_FRAME, TOPIC_SYSTEM_EVENT,
    TOPIC_ERROR,
)


def _now_ms() -> int:
    return int(time.monotonic() * 1000)


@dataclass(slots=True)
class Envelope:
    """A single IPC event with version, timestamp and source unit."""

    topic: str
    data: dict[str, Any]
    source: str
    ts_ms: int = field(default_factory=_now_ms)
    v: int = PROTOCOL_VERSION

    def to_wire(self) -> dict[str, Any]:
        """Serialise to the JSON shape used on the wire."""
        return {
            "v": self.v,
            "ts_ms": self.ts_ms,
            "source": self.source,
            "topic": self.topic,
            "data": self.data,
        }

    @classmethod
    def from_wire(cls, raw: dict[str, Any]) -> "Envelope":
        """Parse a wire-format dict into an :class:`Envelope`."""
        return cls(
            topic=raw["topic"],
            data=raw.get("data") or {},
            source=raw["source"],
            ts_ms=raw["ts_ms"],
            v=raw.get("v", PROTOCOL_VERSION),
        )


def wake_detected(
    *, source: str, model: str, score: float, language: Literal["en", "pl"]
) -> Envelope:
    """Build a `wake.detected` envelope."""
    return Envelope(
        topic=TOPIC_WAKE_DETECTED,
        source=source,
        data={"model": model, "score": score, "language": language},
    )


def system_event(*, source: str, kind: str, detail: str | None = None) -> Envelope:
    """Build a `system.event` envelope."""
    payload: dict[str, Any] = {"kind": kind}
    if detail is not None:
        payload["detail"] = detail
    return Envelope(topic=TOPIC_SYSTEM_EVENT, source=source, data=payload)


def error_event(
    *, source: str, code: str, message: str, hint: str | None = None
) -> Envelope:
    """Build an `error` envelope."""
    payload: dict[str, Any] = {"code": code, "message": message}
    if hint is not None:
        payload["hint"] = hint
    return Envelope(topic=TOPIC_ERROR, source=source, data=payload)


__all__ = [
    "PROTOCOL_VERSION",
    "ALL_TOPICS",
    "Envelope",
    "asdict",
    "error_event",
    "system_event",
    "wake_detected",
    "TOPIC_AUDIO_FRAME",
    "TOPIC_WAKE_DETECTED",
    "TOPIC_VAD_START",
    "TOPIC_VAD_END",
    "TOPIC_ASR_PARTIAL",
    "TOPIC_ASR_FINAL",
    "TOPIC_NLU_INTENT",
    "TOPIC_BRAIN_REPLY",
    "TOPIC_TTS_FRAME",
    "TOPIC_SYSTEM_EVENT",
    "TOPIC_ERROR",
]
