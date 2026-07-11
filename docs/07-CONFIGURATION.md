# 07 — Configuration

Configuration lives in YAML files. Two namespaces, two write authorities.

```
/etc/blazen/
├── system.yaml              # SSH-only — sets the safety envelope
├── audio.yaml               # mostly voice-mutable
├── asr.yaml                 # voice-mutable subset (model size)
├── tts.yaml                 # voice-mutable subset (voice)
├── llm.yaml                 # voice-mutable subset (model)
├── wake-word.yaml           # voice-mutable subset (name)
├── intents/
│   ├── system.yaml          # voice-mutable
│   └── plugins/*.yaml       # plugin-installed
├── voice-policy.yaml        # which intents may mutate which keys
└── overrides/
    ├── voice.yaml           # written by the voice path
    └── admin.yaml           # written by the SSH path
```

## Layered resolution

At runtime each component resolves config in this order, last-wins:

1. The shipped default (`/usr/share/blazen/defaults/<x>.yaml`) — immutable.
2. The site config (`/etc/blazen/<x>.yaml`) — set by the admin via SSH or
   image build.
3. `/etc/blazen/overrides/admin.yaml` — SSH path overrides.
4. `/etc/blazen/overrides/voice.yaml` — voice path overrides.
5. Environment variables (`BLAZEN_*`) — only for image build / VM.

Any change reloads only the affected component via `systemctl reload`.

## Audio capture + VAD (`audio.yaml`)

`input:` describes the mic path consumed by `blazend-audio-in` → the
shared-memory ring → `blazend-asr` (see `docs/14` §3a):

- `device` — ALSA name hint; reference HW is the **ReSpeaker 2-Mics Pi HAT**
  (WM8960 codec, overlay `wm8960-soundcard`, ALSA card `wm8960soundcard`).
- `sample_rate_hz: 16000`, `channels: 1`, `frame_ms`, `ring_buffer_seconds`,
  `pre_roll_ms` — ring + framing geometry.
- `input.vad:` — **self-calibrating** energy VAD (linear i16 RMS), mirrored by
  the `blazend-audio-in` CLI flags. It learns the ambient noise floor and scales
  the thresholds off it, so it works without per-install tuning:
  - `open_rms` / `close_rms` — *absolute floors* (safety net).
  - `open_mult` / `close_mult` — headroom over the learned ambient;
    `effective_open = max(open_rms, noise_floor × open_mult)`.
  - `hangover_ms` — trailing silence before `vad.end`.
  - `min_speech_ms` — minimum speech before `vad.start` (rejects clicks).
  Only raise the floors if a very loud, steady room still triggers; normally the
  multipliers adapt on their own (watch `noise_floor` in audio-in debug logs).

Related env (image build / dev only): `BLAZEN_REAL_AUDIO=1` enables the real
voice path in `scripts/dev-run.sh`; `BLAZEN_AUDIO_DEVICE` overrides the device
hint; `BLAZEN_ASR_MODEL` overrides `asr.yaml active` (8 GB Pi → `small`);
`BLAZEN_VAD_OPEN`/`BLAZEN_VAD_CLOSE` override the VAD energy floors for a
low-sensitivity mic (see `vad` keys above).

## Output loudness leveling + speech compression (`audio.yaml`)

`blazend-player` (radio / music / audiobooks) runs an output-side dynamics chain
so every source plays at the **same real level** — a quiet Wolne Lektury
audiobook (~-20 LUFS) no longer disappears under radio. The player measures its
**own output** and slews a gain toward the target; `RadioControl` passes the
config as CLI flags (`voice_output/adapters/rpi5/radio_control.py`).

- `leveling:` — **always on, every source.** `target_dbfs` is the output RMS the
  leveler holds; quiet content is lifted up to `max_boost_db`; `limit_dbfs` is a
  brick-wall ceiling so leveling/compression never clip. The gain moves slowly
  when boosting (no pumping) and fast when taming a loud passage. Set
  `enabled: false` (or run the player with `--no-level`) to disable.
- `compression:` — **spoken-word only** (books/podcasts; `speech=True`), **never
  music/radio** (their dynamics are preserved). A downward compressor above
  `threshold_dbfs` at `ratio`, plus `makeup_db`, evens out quiet/loud speech for
  maximum intelligibility.

The player logs the live level every ~5 s (`out_dbfs`, `level_gain_db`) so you can
watch the real output volume it's holding. Manual volume (`głośniej`/`ciszej`,
`audio.volume`) still rides on top via the Jabra ALSA mixer. Not covered here:
Jessica's TTS voice + voice memos play through the separate `blazend-audio-out`
(Piper) service — same compressor is a planned follow-up there.

## Hands-free voice runner (`wake-word.yaml`)

The single-process hands-free loop (`blazend.domains.voice_input.adapters.rpi5.voice.runner`, started by
`scripts/voice-run.sh` / `python -m blazend.domains.voice_input.adapters.rpi5.voice`) owns ASR → engine → Piper
in one process that reads the Rust audio ring directly — only `blazend-audio-in`
and `blazend-wake` run alongside it, so there is no ALSA device contention. It
subscribes to `wake.detected`, plays an acknowledgement, then captures a **fixed
window** straight from the ring (no energy VAD — the quiet WM8960 HAT fragments
short utterances), transcribes Polish-first/English-fallback, routes through the
assistant engine, and speaks the reply; due reminders fire on a 1 s ticker. The
HAT button delimits its own held window as a push-to-talk fallback.

- `wake-word.yaml capture_window_s` (default `4.5`) — the post-wake capture
  length in seconds. Env override: `BLAZEN_CAPTURE_S`.
- `wake-word.yaml require_wake` / `conversation_window_s` — wake gating: each
  acted-on utterance re-opens the follow-up window.
- Runner env (rig/dev only): `PTT_OUT` (ALSA `aplay -D` output device),
  `BLAZEN_PIPER` (piper binary), `BLAZEN_BUTTON=0` to disable the GPIO button,
  `BLAZEN_BUTTON_CHIP` / `BLAZEN_BUTTON_LINE` (default `gpiochip0` line `17`).
- Status-LED env (HAT APA102 over SPI0, `blazend/led_hw.py`; fail-soft to a
  no-op when there's no SPI device): `BLAZEN_LED=0` disables the hardware LED;
  `BLAZEN_LED_BUS` / `BLAZEN_LED_DEV` (default `0` / `0` → `/dev/spidev0.0`),
  `BLAZEN_LED_COUNT` (default `3`), `BLAZEN_LED_BRIGHTNESS` (0–31, default `8`),
  `BLAZEN_LED_ORDER` (default `bgr`; set e.g. `rgb`/`grb` if a clone HAT shows
  the wrong colours). Cycle every colour to check wiring: `python -m
  blazend.domains.systems.adapters.rpi5.led_hw`.

## Startup greeting + capabilities (`system.yaml`)

`startup_greeting` makes Jessica introduce herself once, spoken by the orchestrator
when the pipeline comes up (so a screenless user hears the system is alive). It is
**Polish-first** and uses `languages.default`:

```yaml
startup_greeting:
  enabled: true
  delay_s: 8          # grace for TTS + audio-out to subscribe before speaking
  pl: "Cześć, tu Jessica. Jestem gotowa do pomocy."
  en: "Hi, I'm Jessica. I'm ready to help."
```

**"What can you do?"** is a fast-path intent (`what_can_you_do` in
`intents/system.yaml`, `action: say`) that speaks a canned bilingual capability
summary instantly; deeper "how does X work?" follow-ups fall through to the brain,
whose `llm.yaml` system prompt lists the same functions so it can explain + advise.

## Conversation engine — local LLM + cloud layers

Freeform chat is **on-device first**: `blazend.domains.local_ai.adapters.rpi5.localllm.LocalLlm`
loads the GGUF named by `llm.yaml active_model` (override `BLAZEN_LLM_MODEL`)
and answers locally. Model files resolve to `<models>/llm/<active>/<file>`,
where `<models>` is `BLAZEN_MODELS_DIR` (else `<repo>/models`) and `<file>` is
`models.<active>.cpu.file` from `llm.yaml` — the exact layout
`scripts/install_models.py` writes. The runtime binding (`llama-cpp-python`,
the `runtime` extra) is imported lazily, so a host without it (or without the
GGUF) simply reports the local engine as unavailable and falls through.

Backend selection is **task-based** via the brain's `ModelRouter`, configured in
`llm.yaml` `routing:` — `command`→Bielik 1.5B, `recommend`→Bielik 4.5B,
`open_qa`→GPT-5.5, all preferring the LAN Ollama 11B when reachable, then Gemini,
then a canned reply. See [`05-MODELS.md`](05-MODELS.md#task-based-routing-the-brains-modelrouter)
for the full table and the book/music RAG. Cloud layers activate only when their
key is set (sourced from `/etc/blazen/secrets.env`): `OPENAI_API_KEY`
(+ `OPENAI_MODEL`, e.g. `gpt-5.5`) is used **only for open questions**;
`GEMINI_API_KEY` (+ `GEMINI_MODEL`) remains the path for web-grounded
**news/site** lookups. Commands and recommendations never leave the box.

## Internet info — weather + news

Two explicit, user-initiated web lookups (Polish-first):

- **Weather (`weather.yaml`)** — the "jaka pogoda" / "what's the weather"
  intent, served by [Open-Meteo](https://open-meteo.com): **keyless**, free,
  plain HTTP+JSON (not a cloud LLM), so it fits the on-device contract.
  `default_location` is **Kraków** (used when no city is named); other cities
  resolve via Open-Meteo geocoding when `allow_geocoding: true`. `units`
  (`metric`|`imperial`) flips °C/km/h ↔ °F/mph. Answered locally — never the
  chat model. (`blazend/assistant/weather.py`.)
- **Rain forecast (`weather.yaml`, same file)** — a **dedicated** intent for
  "czy będzie padać?" / "kiedy?" / "czy wziąć parasol?" / "a jutro?" (and the
  English equivalents), matched **before** general weather so a rain question
  leads with the **chance of rain** — never a full conditions dump. The reply is
  rain-first: *"Szansa opadów dziś N%. Najwięcej koło H:00. Jutro M%."*
  `forecast_days` (default 2) covers today + tomorrow so "a jutro?" is
  answerable; `hourly_window_h` (default 8) is how far ahead the peak-hour is
  scanned; `rain_peak_threshold` (default 40 %) is the chance below which the
  "najwięcej koło…" clause is omitted (nothing worth timing). If the provider
  returns **no rain data**, Jessica says *"Nie mam dostępu do prognozy opadów."*
  rather than guessing. Keyless, on-device. (`weather.py rain()` +
  `tools.rain_forecast`.)
- **News (`news.yaml`)** — the "co w wiadomościach" / "what's in the news"
  intent, a **news-of-the-day brief in three tiers**: Kraków → kraj → świat.
  The **data is always keyless RSS** from `news.yaml`'s `tiers:` — real feeds,
  no LLM, the on-device floor:
  - `local` — Kraków (Radio Kraków, Onet Kraków, Gazeta Wyborcza Kraków);
  - `national` — Poland (PAP, Onet, TVN24, Polsat);
  - `world` — international agencies (**Guardian, BBC, CNN, AP**) — English;
  - `world_pl` — Polish-language world coverage (the keyless floor for the
    world tier, so the brief stays fully Polish offline).

  Each tier merges all its feeds, de-duplicates, and caps at `max_per_tier`. A
  dead feed is skipped, never fatal (PAP / Wyborcza-national public RSS are
  currently unstable — kept best-effort with working Polish sources beside
  them). When an **OpenAI (or Gemini) key** is present the collected headlines
  are handed to the model, which **composes the spoken Polish brief and
  translates the English world agencies** — opt-in, strict-improvement. With no
  key the brief reads the Polish tiers natively (`world` → `world_pl`), so it
  works fully on-device and stays Polish. Markdown, URLs and citations are
  stripped before TTS; cloud/API error detail is logged, never read aloud.
  Feeds that mislabel their charset (Radio Kraków declares UTF-8 but ships
  ISO-8859-2) are decoded with a legacy fallback. (`blazend/assistant/news.py`.)

Both are explicit web lookups, consistent with the privacy model (no telemetry;
the user asked for fresh external data). The **time/date** intent stays fully
local — the Pi's NTP-synced clock, no network.

## Internet radio (`radio.yaml`)

Jessica can stream internet radio on request: "włącz Trójkę", "puść Radio
Kraków", "play the radio". `radio.yaml` is the catalogue — each station has a
`name` (what she says), spoken `aliases` (PL + EN; matched accent- and
inflection-insensitively, so "Trójkę"/"trojka" both resolve), a verified stream
`url`, and optional `tags`; one is `default: true` (Trójka). A bare "włącz
radio" makes Jessica **offer** the headline stations and ask which one. Shipped
stations include Trójka + Polskie Radio Jedynka/Dwójka/Czwórka, Radio Kraków
(+ OFF Radio Kraków, RK Kultura), RMF FM and Radio Nowy Świat.

Playback is a plain audio stream → **`blazend-player` (Rust) → ALSA**
(`blazend/voice/runner.py` `StreamPlayer` spawns the unit); no cloud LLM. The
player decodes mp3/aac/flac/ogg/wav with pure-Rust symphonia into a
**prebuffered jitter buffer** before the ALSA write loop, so low-bitrate /
jittery streams play without the underrun stutter the old ffmpeg path produced;
the same unit also plays **local recordings** (any file path as the source).
The `player:` block in `radio.yaml` tunes it — `prebuffer_ms` (default 1500,
buffered before playback starts), `buffer_ms` (4000, jitter-buffer depth) and
`alsa_buffer_ms` (500, ALSA hardware buffer). One station at a time, and any
spoken command frees the speaker (stops the stream) so Jessica can answer. Env:
`BLAZEN_PLAYER` overrides the player binary; the ALSA device is the runner's
`PTT_OUT`. (ffmpeg is no longer required for radio.)

## Personal memory + semantic recall (`embeddings.yaml`)

Jessica remembers **titled, long-form notes** dictated by voice — say
*"zapamiętaj: \<tytuł\>. \<treść…\>"* / *"remember: \<title\>. \<content…\>"*
(hold the HAT button for a long body; the one-shot form works for short notes).
Each note is stored in `memory.json` (text + `title`) on the SD card, and is
**embedded once** so that later questions retrieve the relevant notes and inject
them into the LLM's system prompt — the same `system=` seam covers the local
LLM, OpenAI and Gemini. See `docs/12-ML-ACCELERATOR.md` for the model on the CPU
path; a body with no sentence break stays a single untitled note (the original
behaviour).

The embedder (`blazend.domains.context.adapters.rpi5.embeddings.Embedder`) loads the ONNX model
named by `embeddings.yaml active_model` via `onnxruntime` + a `tokenizers` fast
tokenizer (both the `runtime` extra, imported lazily). Files resolve to
`<models>/embeddings/<active>/{model.onnx,tokenizer.json}` — the layout
`scripts/install_models.py` writes (a model entry uses a `files:` list so both
files land in one directory). **The CPU path is the contract:** if the model or
the deps are absent the engine **degrades to lexical note recall**
(`MemoryStore.recall`), so nothing breaks — embeddings are a strict-improvement
path. Default model: `multilingual-e5-small` (384-dim, Polish + English).

- `embeddings.yaml active_model` (default `multilingual-e5-small`) — voice-mutable.
- `embeddings.yaml notes_context.enabled` (default `true`) — master switch for
  injecting retrieved notes into chat. `false` → lexical recall only.
- `notes_context.top_k` (default `4`) — max notes injected per question.
- `notes_context.min_score` (default `0.82`) — absolute cosine floor: the
  **best** match must clear it, otherwise nothing is injected (handles the
  "no relevant note" case). e5 cosines sit in a compressed high band (related
  ≈ 0.84–0.90, unrelated ≈ 0.75–0.83), so this floor is tuned just below the
  related band, not to a 0–1 scale. Model-specific — retune if you swap models.
- `notes_context.rel_margin` (default `0.06`) — keep only notes within this
  cosine margin of the top hit, isolating a clear winner from near-ties (a flat
  threshold can't, since a relevant 0.84 and an irrelevant 0.83 overlap).
- `notes_context.max_chars` (default `1200`) — character budget for the injected
  block (~300 tokens of the 4096-token window).
- `embeddings.yaml e5_prefixes.{query,passage}` — the e5 asymmetric-retrieval
  prefixes (model contract; questions embed as `query:`, notes as `passage:`).

Vectors live in a sidecar `note_embeddings.json` next to `memory.json` (keeps
the note store human-readable); a change of `active_model` invalidates them and
they are re-embedded lazily at startup.

## Voice-policy file

`voice-policy.yaml` is the **single source of truth** for what the user
can change by voice. Sample:

```yaml
version: 1
allow_voice_mutation:
  # key dotted-path -> {confirm: never|single|loud, also_writes: [paths]}
  audio.volume:
    confirm: never
  asr.model:
    confirm: single
    allowed_values: [tiny.en, base.en, small.en, medium.en]
  llm.model:
    confirm: single
  tts.voice:
    confirm: never
  wake_word.name:
    confirm: single
  system.wifi.ssid:
    confirm: single
  system.power.reboot:
    confirm: loud
  system.power.shutdown:
    confirm: loud
  system.factory_reset:
    confirm: double_loud
  ssh.enabled:
    confirm: loud
  telemetry.enabled:
    confirm: loud
deny_voice_mutation:
  - system.firewall.*
  - system.users.*
  - system.image.*
  - "**.secret.**"
```

- `confirm: never` — applied immediately.
- `confirm: single` — assistant repeats the change and asks "should I
  apply it?".
- `confirm: loud` — assistant repeats the change, plays a tone, asks
  the user to say the phrase verbatim ("apply change").
- `confirm: double_loud` — two `loud` confirmations 5 s apart.
- `confirm: timed_window` — only allowed within 5 s after wake word
  (reserved for future destructive ops).

## Mutations from voice — execution model

1. NLU classifies an utterance as a `config_mutation` intent.
2. The intent payload is `{ key: dotted.path, value: any }`.
3. `blazend-config` checks `voice-policy.yaml`:
   - Deny list match → "I'm not allowed to change that. Use SSH."
   - Allow list match → step through the configured confirm flow.
4. On approval, write to `overrides/voice.yaml` and `systemctl reload`
   the affected unit.
5. Echo the new value back ("volume is now 60 percent").

`overrides/voice.yaml` is a single flat YAML keyed by dotted paths so it
diff-reviews cleanly over SSH.

## Schema validation

`configs/_schema/*.json` holds JSON Schema for every config file. The
loader (`blazend.config.Loader`) validates on every read. Invalid config
files refuse to apply and leave the previous value in place; the user
hears "I tried to change that but the configuration was rejected; the
old value is still in effect."

## Secrets

Anything matching `**/secret.*` or `**.secret.*` is voice-deny. Secrets
live in `/etc/blazen/secrets/*.yaml`, mode `0600`, root-only readable.
Loaded by `blazend-config` at start and re-read on SIGHUP. The voice
path never sees them; the LLM only sees redacted placeholders.

## Example: full system.yaml

```yaml
version: 1
hostname: blazen
locale: pl_PL.UTF-8
timezone: Europe/Warsaw
languages:                             # see docs/13-LANGUAGES.md
  enabled: [pl, en]
  default: pl
  detection:
    min_confidence: 0.6
    fall_back_to_wake_hint: true
  pinned: null
packages:                              # SSH-only
  base_image_sha: <pinned>
  pinned:
    pipewire: 1.0.7
    python3: 3.11.6
    faster-whisper: 1.0.3
    llama-cpp-python: 0.3.5
    piper: 1.2.0
    openwakeword: 0.6.0
power:
  reboot_grace_seconds: 3
  shutdown_grace_seconds: 5
ssh:
  enabled: false
  port: 22
  recovery_mode_window_minutes: 30
firewall:
  default_policy: deny_incoming
  allow_from_lan: [22]
telemetry:
  enabled: false                       # never on by default
  endpoint: ~
voice_recovery_thresholds:             # see docs/06-SSH-BOOTSTRAP.md §3
  audio_in_silent_consecutive: 3
  audio_out_silent_consecutive: 3
  brain_no_token_consecutive: 3
```

## Why YAML?

YAML diffs are readable over SSH and human-editable when needed. The
write authority distinction (voice vs SSH) lets us keep destructive
config out of the voice path without giving up the flexibility of
voice-changeable comfort settings.
