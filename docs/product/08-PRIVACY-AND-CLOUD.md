# 08 — Privacy and cloud

Jessica handles personal data: voice, email, messages, calendar.
Every implementation MUST honour the policy in this doc. Violations
are P0 bugs.

## Default state

| Class                  | Default | Notes                                              |
|------------------------|---------|----------------------------------------------------|
| Wake-word detection    | local   | Always on-device. Never streams.                   |
| ASR (speech → text)    | local   | Whisper on Pi, Apple Speech / Google Speech on mobile. |
| Intent routing         | local   | Regex + small classifier — no cloud touch.         |
| Reply generation       | local first, cloud-on-demand | See routing below. |
| Email / Facebook fetch | cloud   | Necessary to access the data; per-intent.          |
| TTS                    | local   | Piper / Apple AVSpeechSynth / Android TTS.         |
| Voice ID embeddings    | local   | Never leave the device.                            |
| Conversation memory    | local   | Local summarisation by on-device LLM.              |
| Telemetry              | off     | Opt-in only (anonymous error counts).              |
| Crash reports          | off     | Opt-in only.                                       |

## Reply generation routing

```
user utterance
   │
   ▼
local NLU fast-path  ────►  intent matched? (volume, time, stop, ...)
   │                          ├── yes → local action, done.
   │                          └── no  → continue
   ▼
local LLM (Qwen 2.5 3B Q4)  ──►  short + conversational?
   │                          ├── yes → reply locally.
   │                          └── no  → escalate
   ▼
cloud router (per cloud_policy.yaml)
   │
   ├── allowed AND online ──► Gemini Flash / Pro / Deep Research
   └── blocked OR offline ──► "I'd need internet for that, want me to wait?"
```

The cloud router checks `cloud_policy.yaml` (next section) **before
every cloud call**. Calls are queued, never fire concurrently in the
background.

## `cloud_policy.yaml`

```yaml
version: 1
cloud_enabled: true                # global kill-switch
daily_cap_usd: 0.50
hourly_call_cap: 60
per_intent:
  gemini.ask:           { allowed: true,  loud_confirm: false }
  gemini.deep_research: { allowed: true,  loud_confirm: false }
  email.fetch:          { allowed: true,  loud_confirm: false }
  email.send:           { allowed: true,  loud_confirm: true  }
  facebook.fetch:       { allowed: true,  loud_confirm: false }
  facebook.post:        { allowed: true,  loud_confirm: true  }
  news.fetch:           { allowed: true,  loud_confirm: false }
  webpage.fetch:        { allowed: true,  loud_confirm: false }
  podcast.search:       { allowed: true,  loud_confirm: false }
quiet_hours:
  start: "22:30"
  end:   "06:30"
  policy: prompt_first    # never silent surprises after 22:30
```

The user can mutate:

| Voice phrase                                | Effect                                |
|---------------------------------------------|---------------------------------------|
| "Jess, nie dzwoń do Google przez godzinę"   | `cloud_enabled = false` for 1h        |
| "Jess, włącz tryb internetu"                | Re-enables cloud immediately          |
| "Jess, dzisiaj bez Facebooka"               | `per_intent.facebook.* = false` until next reboot |
| "Jess, ciche godziny od 22 do 7"            | Updates `quiet_hours`                 |
| "Jess, koniec cichych godzin"               | Disables quiet hours                  |

## Audit log

Every cloud call is recorded:

```
/var/lib/jessica/cloud-audit.log              # appliance, rotated daily
~/Library/.../jessica/cloud-audit.log         # iOS app sandbox
/storage/emulated/0/Android/data/.../audit.log # Android scoped storage
```

Format (JSON Lines):

```json
{ "ts": "2026-06-12T08:14:33Z",
  "intent": "gemini.ask",
  "endpoint": "generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
  "bytes_sent": 1240,
  "bytes_received": 980,
  "latency_ms": 1850,
  "cost_micro_usd": 38,
  "trigger": "voice",
  "transcript_redacted": "…"
}
```

Transcripts are **always redacted** in the audit log (PII names, email
addresses, phone numbers → `<redacted>`).

User can ask:

> "Jess, ile razy dzwoniłaś dziś do Google?"
> > "Czternaście. Najwięcej około 9:30 podczas porannego briefingu."

> "Jess, pokaż mi co Google wiedziało o moim ostatnim pytaniu."
> > Reads back the **redacted** payload.

## Secret storage

| Implementation | Where                                              |
|----------------|-----------------------------------------------------|
| Appliance      | `/etc/jessica/secrets/*.yaml`, `0600 root:root`. Read by `blazend-config` only. |
| iOS            | Keychain Services with `kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly`. |
| Android        | EncryptedSharedPreferences (Tink-backed) + Keystore. |

No secrets in logs, ever. Schemas marked `secret: true` are redacted
at log time.

## Voice and audio retention

- Wake-word audio: discarded after detection. **Never stored.**
- Utterance audio: discarded after ASR transcript is produced.
  Exception: "full mode" voice notes (the user explicitly said
  "keep the recording" / "zapisz nagranie").
- TTS output: discarded after playback.
- Voice ID embedding training data: deleted after training; only the
  embedding survives.

## Data-export and -delete

Voice-triggered:

```
"Jess, daj mi wszystko, co o mnie wiesz"
  → produces /tmp/jessica-export-<date>.zip with: notes, reminders,
    audit log, profile yaml, embedding (anonymised), settings.
  → on appliance: copies to SSH-accessible /var/tmp/.
  → on mobile: opens share sheet so the user can save to Files / Drive.

"Jess, zapomnij wszystko o mnie"
  → loud-confirm, double-confirm, then wipes ~/.jessica/, mute lists,
    notes DB, reminders DB, profile yaml, audit log, embeddings.
  → does NOT touch the OS-level secrets that the user originally
    granted (they need to revoke via OS settings).
```

## What we never do

- Send raw audio to the cloud.
- Log message bodies in the audit log.
- Train any model on the user's data and ship the weights anywhere.
- Send anonymised telemetry by default.
- Share data between users on the same device (Jessica's per-user
  state is per-user; bricked accounts can't read each other's data).

## Cross-implementation contract

Both implementations expose the same surface for privacy actions:

```ts
interface PrivacyController {
  getCloudPolicy(): Promise<CloudPolicy>;
  setCloudPolicy(patch: Partial<CloudPolicy>): Promise<void>;
  getAuditLog(since: Date): Promise<AuditEntry[]>;
  exportAll(): Promise<{ archivePath: string }>;
  deleteAll(): Promise<{ deletedCounts: Record<string, number> }>;
}
```

Implementations:

- `blazen_os/src/blazend/privacy/controller.py`
- `rachel/lib/privacy/controller.dart`
