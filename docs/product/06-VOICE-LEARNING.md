# 06 — Voice learning (per-user model)

Goal: Jessica should recognise her primary user reliably, ignore
non-primary household voices for "this is me" confirmations, and lower
wake-word false positives over time.

We do **NOT** do speaker verification for security-critical actions
(e.g., "delete my email") — that always requires the loud-confirm
phrase. Voice ID is a comfort feature, not an auth boundary. See
[`08-PRIVACY-AND-CLOUD.md`](08-PRIVACY-AND-CLOUD.md).

## Three layers of personalisation

| Layer                    | What's learned                                | Stored where                  |
|--------------------------|-----------------------------------------------|--------------------------------|
| 1. Wake-word fine-tune   | New threshold per user; better recall.        | `~/.jessica/wake/<user>.onnx` |
| 2. Speaker embedding     | Per-user speaker vector (256-d).              | `~/.jessica/voice/<user>.bin` |
| 3. Conversation register | Vocabulary, sentence length, intent prior.    | `~/.jessica/profile.yaml`      |

## Layer 1 — Wake-word fine-tune

Triggered by: "Jess, naucz się mojego głosu" / "Jess, learn my voice".

Steps:

1. Jessica announces what's about to happen + asks for silence in the
   background.
2. Plays 10 prompts:
   - 4× "Hej Jessico" / "Hey Jessica" (different intonations: high,
     low, fast, slow).
   - 2× "Jess" alone.
   - 4× random distractor sentences for negative samples.
3. Records each utterance, validates VAD start/end.
4. Augments the synthetic Piper training set with these 10 + random
   noise overlays.
5. Retrains the wake model on-device:
   - Appliance (Pi 5 16 GB CPU): ~5 min via small fine-tune over
     openWakeWord backbone.
   - iPhone 15/16 Pro (Neural Engine): ~2 min via CoreML on-device
     training.
   - Pixel 9 Pro (Tensor G4 AICore): ~3 min via TFLite-based
     on-device fine-tune.
6. Validates on a held-out positive set + 30 min of background audio,
   reports the false-positive rate. If FPR > 0.1 / hour, asks for 5
   more samples.
7. Picks a per-user wake threshold (default global ≈ 0.6, per-user is
   typically 0.50-0.55 for the primary user — tighter recall).

Multiple users can train separate profiles. The active profile is the
**most-recently-detected speaker** in the last 5 minutes (see Layer 2).

## Layer 2 — Speaker embedding

A small speaker-recognition model produces a 256-d embedding per
utterance. Used to:

- Identify the speaker probabilistically and apply their wake-word
  profile.
- Surface only their content ("Adam, jest jeden mail dla Beaty —
  pominę go").
- Optionally route the "this is me" voice-confirm for personal
  actions (`confirm: voice_id_match`).

Backend:

| Platform | Model                                            |
|----------|---------------------------------------------------|
| Appliance| `wespeaker-voxceleb-resnet34-LM.onnx` (~25 MB, ~30 ms on Pi 5) |
| iOS      | `SoundAnalysis` + a custom `SNClassifier` fine-tuned per user (Neural Engine) |
| Android  | `mediapipe-tasks-audio` (TFLite, on-device) |

Embeddings are written to `~/.jessica/voice/<user>.bin` with a stable
key (slug of the user-given name). Comparison: cosine similarity ≥
0.78 → "probably this user".

## Layer 3 — Conversation register

A tiny per-user YAML keeping track of:

```yaml
language: pl                               # primary spoken language
register: casual                           # casual | formal
common_phrases:                            # extracted from history
  - "jak zwykle"
  - "ok puść"
vocab_preferences:
  units: metric
  date_format: "DD MMMM YYYY"
  time_format: 24h
intent_weights:                            # bayesian prior — what's likely
  email.read_latest: 1.4
  podcast.play: 1.2
  fb.read_feed: 0.6                        # they barely use it
mute_list:
  - "wujek-x"                              # never surface in briefing
  - "klub-koleżeński"
```

Updated lazily; never re-uploaded anywhere. Inspected via "Jess, co
o mnie wiesz?" (US-21).

## Onboarding flow

First boot (after pairing — see `blazen_os/docs/06-SSH-BOOTSTRAP.md`):

1. Jessica says (in PL by default per primary user):
   > "Cześć, jestem Jessica. Możesz mi mówić Jess. Jak się nazywasz?"
2. User answers. Jessica writes the name to the profile and uses it
   from now on.
3. Asks: "Chcesz mnie nauczyć rozpoznawać Twój głos? Zajmie nam to
   około pięciu minut."
4. If yes → Layer 1 retraining flow.
5. If no → continues with the default global wake threshold; offers to
   train again any time.

Mobile-specific:

- During onboarding the app asks for **microphone permission** (always),
  **speech recognition** (always), **notifications** (for briefing),
  **calendar** (optional), **contacts** (optional, only if US-20
  enabled), **photos/files** (only for share-sheet integration).

## Why we're conservative about "voice ID = auth"

Imitation attacks (deepfake voice clones) make voice-ID-as-auth
unsafe. Our rule:

- Anything **read-only** is fine to gate by voice ID.
- Anything **destructive** (send email, post comment, delete note,
  factory reset) requires the spoken confirm phrase regardless of
  voice ID.

The voice ID makes the spoken confirm phrase **shorter** for the
primary user (one "potwierdzam" instead of "potwierdzam i wykonaj").
But it never **removes** the confirm.

## Quick reference (PL)

Jessica uczy się głosu na trzy sposoby: dotreniowuje model wake-word
(żeby pewniej budziła się na Twój głos), tworzy 256-wymiarowy
"odcisk" Twojego głosu (żeby wiedzieć, kto pyta), i prowadzi krótki
profil Twoich preferencji językowych. Cały proces odbywa się
lokalnie — embedding i profil **nigdy** nie trafiają do chmury.
Rozpoznanie głosu nie zastępuje słownego potwierdzenia przy
destrukcyjnych komendach (np. "wyślij maila") — to dla wygody, nie
dla bezpieczeństwa.
