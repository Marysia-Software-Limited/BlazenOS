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
| `blazend-player`           | Rust   | `symphonia`, `alsa`, `ureq` | Internet-radio + local-file player. Decodes mp3/aac/flac/ogg/wav into a prebuffered jitter buffer → ALSA (fixes stream stutter; replaces the old ffmpeg path). Spawned by the Python runner (`StreamPlayer`). |
| `blazend-wake`             | Rust   | `ort` (ONNX Runtime)   | openWakeWord ONNX loop; loads N models in parallel.    |
| `blazend-tts`              | Rust   | `piper-rs` or wrapped  | Streaming PCM out, voice swap on language change.      |
| `blazend-health`           | Rust   | `tokio`, `systemd`     | Watchdog; talks to systemd notify socket.              |
| `blazend-nlu`              | Rust   | `jessica-core`         | Fast-path intent router: `asr.final` → `nlu.intent` over the **shared** `jessica-core` `IntentRouter` (same crate the iOS/Android apps use via `jessica-ffi`). No Python copy. |
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
  - Rust types: `domains/blazend-ipc/build.rs` runs `typify` against the
    schemas and emits `events.rs`.
  - Python types: `scripts/gen-event-types.py` runs `datamodel-code-generator`
    and emits `src/blazend/events/_generated.py`.
- **Versioning:** every event carries `{"v": 1, ...}`. Adding a field is
  non-breaking; removing one bumps the version and forces a coordinated
  update. The schema CI job rejects breaking changes that don't bump.

Net effect: a Rust component cannot send a message the Python side
silently mishandles. The schema rejects it.

### 3a. Audio PCM travels out-of-band (shared-memory ring)

Bulk audio is too heavy for the JSON socket, so PCM rides a **shared-memory
ring buffer** while the socket carries only markers. `blazend-audio-in` (Rust)
is the single producer; `blazend-asr` (Python) is a reader.

- **Ring implementation:** `rpi5/voice-input/blazend-audioring/` (Rust
  `RingWriter`/`RingReader` + `LinearResampler`) and its byte-identical Python
  twin `rpi5/src/blazend/audio/__init__.py`.
- **Layout** (little-endian, both sides must agree):
  `magic "BZAR" | version | sample_rate_hz | channels | capacity_frames | _pad
  | write_pos:u64 (atomic) | i16[capacity_frames]`. Mono.
- **Two rings, same format:**
  - **Input** — `audio-ring.shm` (16 kHz): producer `blazend-audio-in` (Rust),
    reader `blazend-asr` (Python). `vad.start`/`vad.end` bracket an utterance;
    the ASR snapshots `write_pos` at each and transcribes
    `[write_pos − pre_roll, write_pos]`.
  - **Output** — `tts-ring.shm` (22050 Hz): producer `blazend-tts` (Rust, Piper),
    reader `blazend-audio-out` (Rust). `tts.frame {voice, samples}` tells
    audio-out to read the last `samples` frames, resample to the device rate,
    and play.
- **Sync:** `write_pos` is a monotonic total-frames counter; readers index it
  modulo capacity. No PCM ever crosses the JSON boundary — only the metadata.

---

## 4. Repository layout

The monorepo is split into a **shared core** (top level, common to iOS,
Android and the Rpi5 appliance) and the **Rpi5 appliance project**
(`rpi5/`, the device-only code). The in-repo ios/ and android/ trees consume the
shared core; nothing under `rpi5/` is built into the mobile apps.

```
blazen_os/
├── domains/                     # SHARED CORE Cargo workspace — portable domain libs (all 3 platforms)
│   ├── Cargo.toml
│   ├── blazend-ipc/             # IPC wire / event envelope (lib) — contract
│   ├── blazend-fabric/          # CRDT sync log (lib + appliance binary) — context
│   ├── jessica-core/            # intent + routing + memory model — mind
│   └── jessica-ffi/             # C ABI + JNI over jessica-core (iOS/Android)
├── configs/                     # shared contract + appliance config
│   ├── _schema/events/          #   authoritative JSON Schemas (shared)
│   ├── intents/system.yaml      #   shared intent vocabulary (mobile reads it)
│   └── *.yaml                   #   appliance ML/runtime config
├── docs/                        # incl. docs/product/ (shared spec)
├── scripts/                     # shared tooling (incl. mobile FFI build scripts)
├── rpi5/                        # ── Raspberry Pi 5 APPLIANCE PROJECT ──
│   ├── Makefile                 #   forwards to the root orchestrator
│   ├── pyproject.toml
│   ├── src/blazend/domains/     #   Python adapters by domain (orchestrator, asr, brain, ...)
│   ├── voice-input/             #   Rust adapters: blazend-audio-in, blazend-wake, blazend-audioring
│   ├── voice-output/            #   Rust adapters: blazend-audio-out, blazend-tts, blazend-player
│   ├── ai-orchestrator/         #   Rust adapter: blazend-nlu
│   ├── systems/                 #   Rust adapter: blazend-health
│   ├── crates/                  #   appliance Cargo workspace manifest only (members under the dirs above)
│   │   └── Cargo.toml           #   path-depends on ../../domains (one-directional)
│   ├── stage-blazen/            #   pi-gen overlay
│   └── tests/                   #   unit + component + scenarios + fixtures
├── Makefile                     # root: cross-host sync + build/test orchestration
└── ...
```

Two Cargo workspaces — `domains/` (shared core) and `rpi5/crates/` (appliance) —
plus the Python tree under `rpi5/src/`. The appliance depends on the core
**one-directionally** (`blazend-ipc` etc. by path → `../../domains`); the core
never depends back. The shared `configs/` and `domains/` stay at the repo root so
the in-repo `ios/` and `android/` trees reference them at a stable path. The
appliance crates live under `rpi5/<domain>/` but the workspace manifest stays at
`rpi5/crates/Cargo.toml` (each member carries `package.workspace = "../../crates"`),
so the build invocations and `rpi5/crates/target/` are unchanged. The root
`Makefile` orchestrates both workspaces and the venv; `rpi5/Makefile` forwards the
appliance targets up.

> **Organized by capability domain (Phase 3 complete, 2026-06-29):** the shared
> cores are the portable domain libraries at the repo root (`domains/`); the Pi's
> Rust + Python adapters live under `rpi5/`. The one-directional dependency, the
> two-workspace build, the binary names, and this Python/Rust split are all
> **unchanged** — it was a directory regrouping. Canonical model:
> [`19-DOMAIN-ARCHITECTURE.md`](19-DOMAIN-ARCHITECTURE.md).

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
  has `domains/blazend-ipc/tests/schema_roundtrip.rs`. Both must pass.

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
