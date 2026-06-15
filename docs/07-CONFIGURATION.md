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
- `input.vad:` — energy VAD thresholds (linear i16 RMS), mirrored by the
  `blazend-audio-in` CLI flags:
  - `open_rms` / `close_rms` — speech-start / silence thresholds.
  - `hangover_ms` — trailing silence before `vad.end`.
  - `min_speech_ms` — minimum speech before `vad.start` (rejects clicks).
  Defaults suit the HAT (ambient RMS ~750), but capture gain is hardware/mixer
  dependent — **calibrate `open_rms`/`close_rms` to the install** by watching
  the ring RMS while speaking.

Related env (image build / dev only): `BLAZEN_REAL_AUDIO=1` enables the real
voice path in `scripts/dev-run.sh`; `BLAZEN_AUDIO_DEVICE` overrides the device
hint; `BLAZEN_ASR_MODEL` overrides `asr.yaml active` (8 GB Pi → `small`).

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
