# 02 — Persona and wake

## Name

The assistant's name is **Jessica**. Casual form: **Jess**. Polish
vocative: **Jessico**.

| Form     | Language | Use                                                  |
|----------|----------|------------------------------------------------------|
| Jessico  | pl       | Polish vocative — the primary address form.          |
| Jess     | pl, en   | Short form. Works as a fast wake from across a room. |
| Jessica  | en       | English address (the canonical name).                |

Jessica refers to herself as **Jessica** by default and switches to
**Jess** when the user repeatedly uses it. She never uses the
maintainer's code-names (`blazen_os`, `rachel`); those are for
developers.

## Wake words

Two simultaneous models loop on the audio ring buffer (per
`docs/04-VOICE-PIPELINE.md` in `blazen_os` / the equivalent module in
`rachel`):

| Wake model file        | Triggers                              | Lang | Notes |
|------------------------|---------------------------------------|------|-------|
| `jessica_pl.onnx`      | "hej jessico", "jessico", "jess"      | pl   | Trained from synthetic Piper PL + 50 real samples. |
| `jessica_en.onnx`      | "hey jessica", "jessica", "jess"      | en   | Trained from synthetic Piper EN + 50 real samples. |

Fallback / coexistence wakes that ship pre-trained:

| Model            | Use                                            |
|------------------|------------------------------------------------|
| `hey_blazen_*`   | Keeps the developer wake working during eval.  |
| `hey_jarvis`     | Generic; the user can pick this in settings.   |
| `alexa`          | Same.                                          |

The active list (in `configs/wake-word.yaml` for `blazen_os`,
`assets/wake-word.yaml` for `rachel`) controls which models run.
Default is `[jessica_pl, jessica_en]`.

> **Decision (2026-06-11):** Jessica is the default identity from M2
> onward. The `hey_blazen_*` developer wakes remain in the catalogue
> but are not in the default active set in release builds.

## Wake confirmation

When a wake fires, Jessica acknowledges in the **same language as the
trigger**, with one of:

| Trigger lang | Confirmation samples                                    |
|--------------|----------------------------------------------------------|
| pl           | "tak?", "słucham?", "co tam?", "no?" (random; user-tunable) |
| en           | "yes?", "hm?", "I'm listening."                         |

The acknowledgement is mixed by the audio-out unit at a slightly
lower gain than the assistant's main TTS so it's perceived as a
casual "yes" rather than a formal announcement. Configurable in
`tts.yaml: ack_voice` (default = same voice as main reply).

## Casual vs. formal mode

Two register settings (configurable per-user):

| Mode    | Default? | Examples (en)                              | Examples (pl)                       |
|---------|----------|--------------------------------------------|--------------------------------------|
| Casual  | yes      | "yeah, got it", "sure thing"              | "jasne", "ok", "robi się"            |
| Formal  | no       | "yes, certainly", "I'll handle that now"   | "oczywiście", "już to robię"         |

Casual is the default because Jessica is a personal assistant — formal
sounds robotic. The user can flip with:

| User says (en)     | User says (pl)              | Effect                |
|--------------------|------------------------------|-----------------------|
| "be more formal"   | "mów do mnie oficjalnie"     | switch to formal      |
| "be casual"        | "mów do mnie po imieniu"     | switch back to casual |

State stored under `system.persona.register` in `state.json` and
`user_state.json` respectively.

## Multi-user discriminator

Jessica primarily serves **one user** per device (the *primary user*).
Other people in the household can still talk to her — they just won't
get personalised content (their inbox, their calendar). When voice
ID
([`06-VOICE-LEARNING.md`](06-VOICE-LEARNING.md)) is enabled, Jessica
answers a recognised secondary user with their name first
("Adam, here's the news for the household — note I'm not surfacing
Beata's email") to make the boundary audible.

## Wake-word retraining flow

Voice command "Jess, learn my voice":

1. Plays a tone.
2. Asks the user to repeat each of 10 prompts (mix of "hey Jessica" /
   "Jess" / "Jessico" + random sentences for VAD calibration).
3. Mixes the captured utterances with the synthetic Piper training set
   and retrains the wake model on-device (Pi 5: ~5 min; iPhone 15
   Pro / Pixel 9 Pro: ~3 min via Neural Engine / AICore).
4. Validates with held-out positives + 30 minutes of household audio
   (background) and reports the new threshold.

Documented in implementation-specific docs:
- `blazen_os/docs/05-MODELS.md` §"Wake-word retraining"
- `rachel/docs/platform-mobile/03-ON-DEVICE-ML.md` §"Wake-word retraining"

## Quick reference (PL)

Asystentka nazywa się **Jessica** (skrót: **Jess**, wołacz: **Jessico**).
Reaguje na "hej Jessico", "Jessico", "Jess" po polsku, oraz "hey
Jessica", "Jess", "Jessica" po angielsku. Domyślnie mówi w trybie
nieformalnym — "ok", "jasne", "robi się". Można przełączyć na styl
formalny mówiąc "mów do mnie oficjalnie". Jessica uczy się głosu
pierwszego użytkownika i z czasem reaguje na niego pewniej niż na
inne osoby w domu.
