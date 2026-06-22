# 12 — Optional ML accelerator (LLM path)

`blazen_os` ships a **CPU-only** voice path that runs end-to-end on a
Raspberry Pi 5 8 GB. The CPU path is the contract — every feature must
work without an accelerator. An accelerator, when present, **only**
speeds up the LLM and (optionally) ASR, with the same observable
behaviour.

This document is the contract for plugging in an optional ML accelerator
and how the LLM-based conversation uses it.

> **Note — text embeddings stay CPU-only.** Jessica's personal-memory recall
> (`embeddings.yaml`, `blazend.assistant.embeddings`) runs a small sentence
> embedder (`multilingual-e5-small`, 384-dim) on `onnxruntime` CPU. It is light
> enough (tens of ms per short text) that it does not need the accelerator, and
> if the model/deps are absent the engine degrades to lexical note recall — so
> retrieval-augmented memory respects the same CPU-path-is-the-contract rule.
> See `docs/07-CONFIGURATION.md` → "Personal memory + semantic recall".

## 1. Why optional, not required

- Most home users will not install an accelerator.
- Pi 5 8 GB at full CPU runs Qwen2.5 3B Q4 at ~12 tok/s — usable.
- An accelerator turns the assistant from "usable" into "snappy" and
  unlocks larger / more capable models.
- Hard-requiring a HAT would lock out users and complicate the
  hardware story (`docs/02-HARDWARE.md`).

## 2. Supported accelerators

The reference accelerator is the **Raspberry Pi AI HAT+ / AI Kit**
family (Hailo silicon). They expose a PCIe Gen 3 ×1 link to the Pi 5
through the standard PCIe FFC connector. Vendor-supported variants:

| Variant       | Silicon  | TOPS | Form-factor                 | LLM fit | Vision fit | Notes |
|---------------|----------|-----:|------------------------------|--------:|-----------:|-------|
| AI Kit        | Hailo-8L | 13   | M.2 2230 in HAT+ M.2 carrier | OK      | Excellent  | Cheapest entry. |
| AI HAT+ 13T   | Hailo-8L | 13   | HAT+ board                   | OK      | Excellent  | Same chip, integrated form. |
| AI HAT+ 26T   | Hailo-8  | 26   | HAT+ board                   | Good    | Excellent  | Current sweet spot for vision. |
| AI HAT+ 10H   | Hailo-10H| 40   | HAT+ board                   | **Best**| Excellent  | Designed for transformer / LLM. |

> **Decision (2026-06-11):** the **Hailo-10H** (AI HAT+ 10H) is the
> reference accelerator for the LLM path. Hailo-8 / 8L are **supported**
> with smaller / older LLMs and slower TTFT. We assume HailoRT 4.20+.

Other accelerators (Coral USB Edge TPU, NCS2, generic NPUs) are **not
in scope** for the LLM path:

- Edge TPU has no LLM toolchain in the Raspberry Pi 5 form-factor.
- NCS2 is end-of-life.
- Generic NPUs lack mature `llama.cpp`-compatible quantisation paths.

If a different accelerator gains a credible LLM toolchain (e.g., AMD
Versal, NXP i.MX 95), revisit this table.

## 3. How the LLM-based conversation uses the accelerator

The LLM is the bottleneck in the voice pipeline (`docs/04-VOICE-PIPELINE.md`).
Two latency numbers dominate:

1. **TTFT** — time to first generated token (drives "is it stuck?" feel).
2. **tok/s** — sustained throughput (drives total reply duration).

The accelerator helps both. With Hailo-10H + Qwen2.5 3B:

| Metric          | Pi 5 CPU | Pi 5 + Hailo-8 (26T) | Pi 5 + Hailo-10H |
|-----------------|---------:|---------------------:|-----------------:|
| TTFT            | 350 ms   | ~180 ms              | ~120 ms          |
| tok/s           | 12       | ~20                  | ~35              |
| 40-tok reply    | ~3.3 s   | ~2.0 s               | ~1.1 s           |
| **wake → first TTS sample** | ~1.3 s | ~1.1 s   | ~0.9 s           |
| RAM resident (LLM) | 2.7 GB | ~2.0 GB             | ~1.8 GB          |

(Vendor figures; we re-measure on the bench during M9.)

> All numbers in `docs/01-ARCHITECTURE.md` and `docs/05-MODELS.md` are
> for the **CPU** path. The accelerated path is a strict improvement
> within the same observable behaviour.

### What runs where

```
                ┌──────────────────────┐
                │  blazend-brain       │
                │  (Python orch)       │
                └──────┬───────────────┘
                       │ generate(prompt)
              ┌────────┴─────────┐
              │ engine selector  │   <- configs/llm.yaml: active_engine
              └─┬──────────────┬─┘
                │ "cpu"        │ "hailo"
                ▼              ▼
   ┌─────────────────┐   ┌──────────────────────────┐
   │ llama-cpp-python │   │ HailoRT runtime          │
   │ GGUF Q4_K_M     │   │ pre-compiled .hef weights│
   │ CPU NEON        │   │ Hailo-8 / 8L / 10H       │
   └─────────────────┘   └──────────────────────────┘
              ▲              │
              │              ▼
              └────────fallback on error
```

- `engine selector` is a small Python class in `blazend.brain.engine`.
- The contract is identical (`generate_stream(prompt) -> iter[Token]`).
- On Hailo error (HEF mismatch, OOM, driver fault) we fall back to CPU
  for the next utterance and re-test the device on the one after.
- For tests, we mock the engine at the contract layer so Tier 1 doesn't
  need either runtime installed.

### Model preparation

The CPU path uses **GGUF Q4_K_M** (standard llama.cpp). The Hailo path
needs a **pre-compiled `.hef`** built with the Hailo Dataflow Compiler
(DFC) from an upstream model checkpoint:

```
HF checkpoint  ─▶  ONNX (HF exporter)  ─▶  DFC quantise (INT8)  ─▶  .hef
                                                                    │
                                                  /var/lib/blazen/hailo/llm/
```

This is **not** done on the Pi — it's an offline step run on the dev
host (or by the model vendor). `configs/llm.yaml` references the
resulting `.hef` by URL + SHA. `make models` downloads them just like
the GGUF files; if the SHA is missing we skip the Hailo variant and
the engine selector falls back to CPU.

The compile step is documented in `scripts/compile-hailo-llm.sh` (M7).

### Engine selection rules (`active_engine: auto`)

Defined in `configs/llm.yaml: auto_engine_rules`:

1. **Hailo present** AND the active model has a `.hef` variant → use Hailo.
2. **Otherwise**, if free RAM cannot fit the model on CPU, downshift
   to the next-smaller allowed model.
3. **Otherwise**, use CPU.

The selector logs its decision on startup and on every model switch.

## 4. Effect on the rest of the pipeline

| Stage         | CPU only       | With Hailo-10H              |
|---------------|----------------|------------------------------|
| Wake word     | unchanged      | unchanged (CPU)              |
| VAD           | unchanged      | unchanged (CPU)              |
| **ASR**       | `small.en` CPU | `small.en` CPU; experimental Hailo Whisper-base if `asr.hailo.enabled: true` |
| NLU fast path | unchanged      | unchanged                    |
| **LLM**       | llama.cpp CPU  | Hailo-10H .hef               |
| TTS           | Piper CPU      | Piper CPU                    |

ASR on Hailo is **opt-in and experimental** — `faster-whisper` on CPU
remains the default because it's already <1 s on Pi 5 small.en.

## 5. Installation and bring-up

Required packages (installed by `stage-blazen/`):

- `hailort` (HailoRT runtime, ≥4.20)
- Hailo PCIe kernel driver (matched to RPi OS Bookworm kernel)
- `hailo-firmware` (the `.bin` files for Hailo-8 / 8L / 10H)
- Python bindings: `hailo-platform`, `hailo-model-zoo` (optional)

On boot, `blazend-health` runs:

```
hailortcli scan
```

If any device is found, `/run/blazen/hailo.json` is populated and
`blazend-brain` picks `engine: hailo` per the auto rules. If the scan
errors or returns empty, the file is `{ "present": false }` and
`blazend-brain` stays on CPU.

Power / thermal:

- Hailo-10H typical 4 W. Pi 5 + Hailo-10H sustained pulls ~9–11 W under
  voice load. Keep the 27 W PD PSU.
- The active cooler is **required** above 25°C ambient when the
  accelerator is in use.

## 6. Configuration cheat-sheet

```yaml
# configs/llm.yaml
active_engine: auto                # auto | cpu | hailo
hailo:
  enabled: true                    # gated on device presence anyway
  variant: hailo-10h
  device_id: 0
  runtime: hailort
  swap_on_fail_to_cpu: true
  power_profile: balanced
```

By voice:

- *"Jessica, switch to the accelerator"* → sets `active_engine: hailo`
  (confirmation: `single`).
- *"Jessica, switch to cpu"* → sets `active_engine: cpu`.
- *"Jessica, use eco power"* → sets `hailo.power_profile: eco`.

## 7. Testing the accelerator path

Tier 1 (component): mock the engine — no real device required.

Tier 2 / 3 (QEMU): impossible — QEMU has no Hailo emulation. The CPU
path is exercised exclusively here. The CI does **not** gate on Hailo.

Tier 4 (hardware): the dev rig adds Pi 5 + Hailo-10H. Per release we
re-run the full Tier 3 suite with `active_engine: hailo` on hardware
and record:

- TTFT median + p95
- tok/s median + p95
- Memory peak
- Cold-start swap latency (engine swap CPU↔Hailo)

Per-scenario YAMLs can opt into latency tightening when the engine is
known:

```yaml
expect:
  latency_budget:
    wake_to_first_tts_ms_p95: 1100   # CPU
    wake_to_first_tts_ms_p95_hailo: 700
```

Tier 5 (soak): runs both engines for 12h each on the same hardware.

## 8. Failure modes and recovery

| Failure                                      | What the user hears                            | System action |
|----------------------------------------------|-----------------------------------------------|---------------|
| Hailo driver fails to load                   | "Accelerator unavailable, using CPU mode."     | One-time log; stay on CPU until reboot. |
| `.hef` SHA mismatch                          | "I can't load the accelerator model."          | Stay on CPU; retry download on next `make models`. |
| Inference error mid-utterance (rare)         | "One moment..." then CPU-generated reply.      | Swap to CPU for this utterance; re-test next utterance. |
| Power throttling (Pi 5 USB power inadequate) | "I'm running slow." (when latency budget broken) | Log + LED yellow; suggest 27 W PSU on SSH motd. |
| Both engines refuse to load (corrupt state)  | "Critical error, recovery mode engaged."        | SSH recovery (per `docs/06-SSH-BOOTSTRAP.md`). |

## 9. Known limitations (current state of the world)

- HailoRT model compilation is **offline**: you cannot compile an LLM
  on the Pi itself.
- LLM context lengths >4 k tokens cost disproportionately more on
  Hailo because the KV cache fight RAM bandwidth.
- The .hef format is vendor-specific; switching to a different
  accelerator family means re-compiling everything.
- Some LLMs are not yet supported by the Hailo compiler — when in doubt,
  CPU works for any GGUF.

## 10. Future hooks (not in M1..M8)

- AMD Versal AI Edge (XCVE2302) M.2 carrier — if a Pi-friendly toolchain
  emerges.
- Embedded GPU offload via PCIe (unlikely on Pi 5 — keep an eye on
  AMD W7000-class small cards).
- Distributed inference: handing the LLM off to a sibling Pi over LAN
  when the local accelerator is busy with vision. (Out of scope for now.)
