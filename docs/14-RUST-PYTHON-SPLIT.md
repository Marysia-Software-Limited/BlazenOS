# 14 — Python / Rust split

`blazen_os` is implemented in **two languages**: **Python** and **Rust**.
Each `blazend-*` component is written in exactly one of them. This doc
explains the boundary, why it falls where it falls, and how to decide
when introducing a new component.

> **Decision (2026-06-11):** Python and Rust are the two main project
> languages. No third language enters the core stack. Shell is allowed
> for boot scripts and pi-gen stages. C/C++ is allowed only inside FFI
> shims for upstream libraries we vendor (e.g., Piper, HailoRT).

---

## 1. The rule

A component is **Rust** when *any* of these is true:

1. It runs a tight loop on every audio frame (≤20 ms cadence).
2. The Python GIL would interfere with predictable latency (any path on
   the wake → audio-out critical line).
3. It must be small, single-binary, and never crash (watchdog, IPC).
4. A mature, idiomatic Rust crate exists for the underlying library
   (`cpal`, `ort`, `piper-rs`) and using it doesn't lose features vs.
   the Python alternative.

A component is **Python** when *any* of these is true:

1. It glues together heterogeneous APIs with rapid iteration cost
   (orchestrator, intent routing).
2. The underlying ML library is Python-first and a Rust binding would
   only re-wrap a C/C++ core (`faster-whisper` / CTranslate2,
   `llama-cpp-python`, HailoRT Python).
3. The code is short, run rarely (bootstrap), and Python's stdlib is
   sufficient.

When both languages are plausible, prefer the one the **adjacent
components** already use. Crossing the FFI boundary inside a single
component is a smell — split the component first.

---

## 2. Final assignment per component

| Component                  | Lang   | Key dependency        | Notes |
|----------------------------|--------|------------------------|-------|
| `blazend-ipc` (library)    | Rust   | `tokio`, `serde_json`  | Shared by every Rust binary. Defines event schema in `events.rs`. |
| `blazend-audio-in`         | Rust   | `cpal`, `crossbeam`    | Mic capture, ring buffer in shared memory, VAD-feed.   |
| `blazend-audio-out`        | Rust   | `cpal`, `rodio`        | Speaker playback, ducking, earcons mixer.              |
| `blazend-wake`             | Rust   | `ort` (ONNX Runtime)   | openWakeWord ONNX loop; loads N models in parallel.    |
| `blazend-tts`              | Rust   | `piper-rs` or wrapped  | Streaming PCM out, voice swap on language change.      |
| `blazend-health`           | Rust   | `tokio`, `systemd`     | Watchdog; talks to systemd notify socket.              |
| `blazend-orchestrator`     | Python | asyncio, pydantic, pyyaml | Pipeline supervisor; the conductor.              |
| `blazend-asr`              | Python | `faster-whisper` (CT2) | Streaming partial transcripts; language detection.     |
| `blazend-brain`            | Python | `llama-cpp-python` / `hailort` Python | Engine selector + sampler. |
| `blazend-bootstrap`        | Python | stdlib                 | First-boot pairing flow.                              |
| `blazend-config` (library) | Python | pydantic               | Layered YAML loader; shared by every Python unit.      |

There is **no single Rust component that calls Python** and **no single
Python component that calls Rust**. They communicate exclusively over
the IPC contract (see §3).

---

## 3. The IPC contract is the boundary

Cross-language safety is enforced by the wire format, not by FFI.

- **Transport:** Unix-domain sockets under `/run/blazen/`, length-prefixed
  framed JSON.
- **Schema:** every event type has a JSON Schema under
  `configs/_schema/events/<topic>.schema.json`. The schema is the
  authoritative source.
- **Generators:**
  - Rust types: `crates/blazend-ipc/build.rs` runs `typify` against the
    schemas and emits `events.rs`.
  - Python types: `scripts/gen-event-types.py` runs `datamodel-code-generator`
    and emits `src/blazend/events/_generated.py`.
- **Versioning:** every event carries `{"v": 1, ...}`. Adding a field is
  non-breaking; removing one bumps the version and forces a coordinated
  update. The schema CI job rejects breaking changes that don't bump.

Net effect: a Rust component cannot send a message the Python side
silently mishandles. The schema rejects it.

---

## 4. Repository layout

```
blazen_os/
├── src/                         # Python sources
│   └── blazend/
│       ├── orchestrator/
│       ├── asr/
│       ├── brain/
│       ├── bootstrap/
│       ├── config/              # shared loader
│       ├── events/              # generated + hand-written event helpers
│       └── ipc/                 # Python client for the IPC contract
├── crates/                      # Rust workspace
│   ├── Cargo.toml               # workspace root
│   ├── blazend-ipc/             # shared library
│   ├── blazend-audio-in/        # binary
│   ├── blazend-audio-out/       # binary
│   ├── blazend-wake/            # binary
│   ├── blazend-tts/             # binary
│   └── blazend-health/          # binary
├── configs/_schema/events/      # authoritative JSON Schemas
├── stage-blazen/                # pi-gen overlay (installs both)
└── ...
```

The Python and Rust trees are **siblings**, not nested. Each has its own
build system; the top-level `Makefile` orchestrates both.

---

## 5. Build & toolchain

### Host requirements

- **Python ≥ 3.11**, `python3-venv`. Managed via `.venv/`.
- **Rust ≥ 1.78 (stable)** via `rustup`. The repo pins a `rust-toolchain.toml`
  so everyone uses the same version.
- For Pi cross-compilation: `cross` (or `aarch64-unknown-linux-gnu` target
  + a sysroot for `cpal`/ALSA). The `Makefile` target `make rust-aarch64`
  wraps `cross build --target aarch64-unknown-linux-gnu --release`.

### Make targets

```
make rust              # cargo build --release for the host
make rust-aarch64      # cross-build for Raspberry Pi 5 (aarch64-unknown-linux-gnu)
make python            # creates .venv, installs Python deps
make gen-events        # regenerates Rust + Python event types from the schemas
make build             # both rust + python
make test-fast         # cargo test + pytest unit/component
make dev               # launches the full stack on the dev host (no VM)
```

### Why pinned toolchains

A reproducible image needs reproducible compilers. Both ecosystems pin:

- Rust: `rust-toolchain.toml` sets the channel (`stable`) and the SHA-ish
  components.
- Python: pinned in `configs/system.yaml: python_packages.pinned`.

Image builds fail if either drifts.

---

## 6. Style and conventions

### Python

- `ruff` for lint, `ruff format` for style.
- Type hints required on public functions; `mypy --strict` on
  `src/blazend/`.
- One module per blazend-* unit; the entrypoint is
  `src/blazend/<unit>/__main__.py` so `python -m blazend.<unit>` runs it.

### Rust

- `cargo fmt` + `cargo clippy --all-targets --all-features -D warnings`.
- `#![deny(unsafe_op_in_unsafe_fn)]` at every crate root.
- One binary per blazend-* unit; shared logic lives in `blazend-ipc`
  (event types, framed JSON codec) or per-crate `lib.rs`.
- Async runtime: `tokio` (single-thread flavor unless the workload
  genuinely needs parallelism — audio-in is single-thread, wake is
  multi-thread per model).

### Cross-language

- Errors crossing IPC are typed: `events::Error { code, message, hint }`.
- Logs go to `journald` via `systemd-journal-logger` (Rust) or the
  standard `logging` module with the `journald` handler (Python).

---

## 7. Testing

### Unit

- Python: `pytest tests/unit`.
- Rust: `cargo test -p <crate>`. `make test-fast` runs both.

### Component (Tier 1)

- Python components are tested with mocked Rust peers — `tests/component/`
  has fake servers that speak the IPC contract.
- Rust components are tested with mocked Python peers — each crate has
  an `integration_tests/` directory that uses `serde_json::Value` to
  simulate the orchestrator's commands.

### Schema round-trip

- A `tests/unit/test_event_schemas.py` test loads every schema, generates
  examples, and round-trips them through Python's parser. The Rust side
  has `crates/blazend-ipc/tests/schema_roundtrip.rs`. Both must pass.

---

## 8. Anti-patterns to avoid

- **Don't add a third language to the core.** Go, TypeScript, Zig — if it
  ever feels tempting, write a design doc first and burn it.
- **Don't reach across the FFI boundary inside a component.** A Python
  component that uses `pyo3` to call into a Rust helper is a smell — the
  helper is its own component.
- **Don't write boot logic in Rust.** Boot logic changes often; shell or
  Python is fine.
- **Don't write tight audio loops in Python.** Even if a quick prototype
  is faster, it will leak into prod and we'll regret it under soak.
- **Don't reinvent the IPC.** Every Rust unit links `blazend-ipc`; every
  Python unit imports `blazend.ipc`. There is exactly one wire format.

---

## 9. Quick reference (PL)

W skrócie:

- Dwa języki kodu źródłowego: **Python** i **Rust**.
- Reguła: hot loop / niska latencja / mała wiarygodna binarka → Rust.
  Glue, asyncio, dojrzała biblioteka ML w Pythonie → Python.
- Komunikacja między językami WYŁĄCZNIE przez kontrakt IPC (framed JSON
  po Unix socket), schematy w `configs/_schema/events/`.
- Audio I/O, wake word, TTS i watchdog są w Rust.
- Orchestrator, ASR, LLM, bootstrap są w Python.
- Build: `make build` robi obie strony naraz. Cross-compile na Pi:
  `make rust-aarch64`.
