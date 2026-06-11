# IPC event schemas

Authoritative JSON Schemas for every event topic. The Rust types in
`crates/blazend-ipc/src/events.rs` and the Python types in
`src/blazend/events/__init__.py` must round-trip every example in
`examples/`.

`scripts/gen-event-types.py` validates this. Once `typify` and
`datamodel-code-generator` are wired into CI (M2), the generators will
overwrite `_generated.{rs,py}` from these schemas directly.

Each schema follows this shape:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "https://blazen.os/events/<topic>",
  "title": "<topic>",
  "type": "object",
  "required": ["v", "ts_ms", "source", "topic", "data"],
  "properties": {
    "v":      { "type": "integer", "const": 1 },
    "ts_ms":  { "type": "integer", "minimum": 0 },
    "source": { "type": "string", "minLength": 1 },
    "topic":  { "type": "string", "const": "<topic>" },
    "data":   { "type": "object", "...": "topic-specific" }
  },
  "additionalProperties": false
}
```

The top-level wrapper is identical across topics; only `data` and the
fixed `topic` literal change.
