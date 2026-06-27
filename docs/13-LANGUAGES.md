# 13 — Languages (PL + EN prototype, Polish-first)

The prototype ships **Polish** and **English** as first-class supported
languages from M1, with **Polish as the primary language** (default,
first-listed, examples-first). English is co-equal and parity-required.
Every voice surface — wake word, ASR, intents, LLM replies, TTS,
confirmation phrases — works in both. The voice-policy and configuration
plumbing is language-tagged so additional languages plug in without code
changes.

> **Decision (2026-06-27):** the appliance now ships **Polish-only at
> runtime** — `system.yaml languages.enabled: [pl]` and `asr.yaml language: pl`
> (model `small`). This is a deliberate narrowing for daily Polish use and
> on-device testing. **The English assets are retained, not deleted:** the
> Lessac TTS voice, the EN wake models, the EN intent triggers, the EN engine
> path, and the EN/mixed scenarios all stay in the tree and keep their tests
> green. EN parity is therefore **deferred** — re-enabling it is a config flip
> (`enabled: [pl, en]`, `asr.language: auto`), not a rebuild. Until then the
> "every surface ships in both" rule below is enforced at the **asset** level
> (configs/code keep their EN counterpart) but not at runtime (only PL is
> active). This supersedes the runtime half of the 2026-06-14 decision; the
> asset-parity half stands.
>
> **Decision (2026-06-14):** **Polish is the primary language** of the
> prototype — it leads in config order (`languages.enabled: [pl, en]`),
> defaults, docs, and examples. English remains a **co-equal,
> parity-required** first-class language: every intent / phrase / scenario
> still ships in both, and a Polish-only **or** English-only surface is
> incomplete. This supersedes the 2026-06-11 "EN and PL are co-equal"
> framing (which left English listed first); functional parity is
> unchanged, only the primacy/order flips to Polish.
>
> _(Superseded — Decision (2026-06-11): EN and PL are co-equal default
> targets; the `small.en` + `en_US-lessac-medium` single-language baseline
> is an optional "EN-only build flavour" and the default prototype ships
> multilingual models out of the box.)_

---

## 1. Why bilingual from day one

- The maintainer is a Polish native speaker who wants daily use in PL while
  retaining EN for testing, demos, and porting to other locales.
- Multilingual ASR + LLM models are now small enough to fit on a Pi 5 8 GB
  alongside two TTS voices.
- Designing for two languages from the start forces a language-tagged data
  flow that scales to N languages cheaply, instead of bolting on i18n later.

---

## 2. End-to-end language flow

```
mic ─▶ wake (per-language model loop) ─▶ ASR (multilingual + lang-id)
                                                 │
                                                 ▼
                                       detected_language: "pl" | "en"
                                                 │
                  ┌──────────────────────────────┼──────────────────────────────┐
                  ▼                              ▼                              ▼
        NLU fast-path (PL+EN triggers)   LLM (bilingual prompt)        TTS voice swap
                                                                       (EN→Lessac, PL→Darkman)
```

Three rules govern the flow:

1. **The reply language matches the detected user language.** Mixed input
   (Polish question with English proper nouns) keeps the detected language.
2. **Language is per-utterance, not per-session.** The user can switch
   between Polish and English freely; the assistant follows.
3. **The user can pin a language** (`"speak Polish"` / `"mów po angielsku"`)
   which disables auto-detection until pinned off.

---

## 3. Stage-by-stage specification

### 3.1 Wake word

Two openWakeWord models loop in parallel:

| Wake phrase  | Language | Model file              |
|--------------|----------|--------------------------|
| "hej Jessico" | PL       | `jessica_pl.onnx`     |
| "hey Jessica" | EN       | `jessica_en.onnx`     |

The wake module emits `wake.detected` with a `language` hint that biases
ASR's first-pass language ID. If a third language is added, append another
model to the loop — `blazend-wake` loads them at startup.

### 3.2 ASR

Default model: `faster-whisper-small` (multilingual, ~466 MB, ~750 ms on
Pi 5 for a 5 s utterance). Drop-in alternatives:

| Model               | PL WER | EN WER | Notes |
|---------------------|-------:|-------:|-------|
| `small` (default)   | 9%     | 6%     | Balanced; the prototype default. |
| `medium`            | 6%     | 4%     | Better PL; +1.2 GB RAM. |
| `large-v3-turbo`    | 5%     | 3%     | Best PL quality on Pi 5 8 GB. |
| `small.en` (EN-only)| n/a    | 5%     | Demoted; only when `lang_mode: en_only`. |

The ASR config (`configs/asr.yaml`) supports `language: auto` (default),
which runs Whisper's built-in detection on the first 1 s of audio, and
falls back to the wake-word language hint on low confidence.

### 3.3 NLU fast-path

Every regex / keyword trigger in `configs/intents/system.yaml` carries
both an `en:` and a `pl:` pattern list. The matcher tries the detected
language first, then the other.

Example:

```yaml
- name: volume_up
  triggers:
    en:
      - "(volume|louder) (up|higher)?"
      - "louder"
    pl:
      - "głośniej"
      - "podgłośnij"
      - "(zrób )?gło(ś|s)niej"
  action: mutate
  mutate:
    key: audio.volume
    delta: +10
```

Confirmation phrases (`apply change`) are also bilingual; the user can say
either `"apply change"` or `"potwierdzam"` / `"zatwierdź"` to clear a
`confirm: loud` gate.

### 3.4 LLM

System prompt is bilingual; the model is instructed to reply in the
detected language:

```
You are Jessica, a helpful voice assistant running on a Raspberry Pi 5.
You are designed to assist blind and visually impaired users.
The user hears you through a speaker; they cannot see a screen.

Reply in the same language the user used. If unsure, default to Polish.
Keep replies short — one or two sentences unless the user asks for detail.
Never invent tool outputs. If you don't know, say so plainly.

[PL] Jesteś Jessicą, asystentką głosową uruchomioną na Raspberry Pi 5.
Twoim zadaniem jest pomaganie osobom niewidomym i słabowidzącym.
Użytkownik słyszy Cię przez głośnik; nie ma ekranu. Odpowiadaj zwięźle
— jedno lub dwa zdania, chyba że poprosi o szczegóły. Nie wymyślaj
wyników narzędzi. Jeśli czegoś nie wiesz, powiedz to wprost.
```

Default model is **Qwen 2.5 3B Instruct (Q4_K_M)** which handles Polish
well; this is unchanged from `docs/05-MODELS.md`. Llama 3.2 3B is the
alternate.

### 3.5 TTS

Two Piper voices are pre-loaded and switched per utterance:

| Language | Default voice            | Alt voices                |
|----------|---------------------------|---------------------------|
| PL       | `pl_PL-darkman-medium`    | `pl_PL-gosia-medium` (optional download) |
| EN       | `en_US-lessac-medium`     | `en_US-amy-medium`, `en_GB-alan-low` |

The TTS engine keeps both voices warm in RAM (~150 MB total) so swap is
free at synthesis time.

### 3.6 Language switching by voice

| User says                               | Effect                                              |
|------------------------------------------|------------------------------------------------------|
| *"mów po polsku"* / *"speak Polish"*     | Pin language to PL until unpinned.                   |
| *"mów po angielsku"* / *"speak English"* | Pin language to EN until unpinned.                   |
| *"słuchaj uważnie"* / *"detect my language"* | Unpin; auto-detect resumes.                      |
| *"Jessico, jakie znasz języki?"* / *"Jessica, what language can you speak?"* | List supported languages.            |

Pinned state is in `/run/blazen/state.json` (`languages.pinned: "pl" | "en" | null`).

**Implemented (M5):** the orchestrator's `IntentDispatcher`
([`blazend/dispatch.py`](../rpi5/src/blazend/dispatch.py)) acts on the
`switch_language` / `unpin_language` / `languages.list` fast-path intents.
A pin wins over the per-utterance detected language for every reply (the
*effective language*), so an English command spoken under a Polish pin still
gets a Polish answer. `switch_language` confirms in the **target** language —
*"Od teraz mówię po polsku."* / *"From now on I'll speak English."* — and
rejects anything outside the co-equal `en`/`pl` set. The pin is persisted in
the voice-settings overlay (`languages.pinned`) and mirrored into
`/run/blazen/state.json`.

---

## 4. Configuration

### 4.1 Top-level language config (in `configs/system.yaml`)

```yaml
languages:
  enabled: [pl, en]                # the set the assistant will accept (Polish first)
  default: pl                      # used when detection confidence < threshold
  detection:
    min_confidence: 0.6
    fall_back_to_wake_hint: true
locale: pl_PL.UTF-8                # OS locale (affects time/date formatting)
timezone: Europe/Warsaw
```

### 4.2 ASR language toggle (in `configs/asr.yaml`)

```yaml
language: auto                     # auto | en | pl
auto_detect:
  first_window_ms: 1000
  switch_threshold: 0.7
```

### 4.3 Per-language defaults (in each component config)

The configs already carry `allowed:` lists. The bilingual set is:

- `wake-word.yaml: allowed: [hey_blazen_en, hey_blazen_pl, hey_jarvis, alexa]`
- `tts.yaml: allowed: [pl_PL-darkman-medium, pl_PL-gosia-medium, en_US-lessac-medium, en_US-amy-medium, en_GB-alan-low]`
- `asr.yaml: allowed: [small, small.en, medium, large-v3-turbo, tiny.en, base.en]`

---

## 5. Polish phonetics caveats

A handful of footguns we have hit in prototypes:

- **Polish nasals (ą, ę)** — Whisper `small` mishears them in fast speech.
  Mitigation: bump to `medium` or `large-v3-turbo` when the user complains;
  documented in the voice command "speak more clearly" → suggests model bump.
- **Diacritic-free dictation** — some users dictate without diacritics. We
  store a normalised form alongside the original so intent regex match both
  ("gloosniej" vs "głośniej").
- **Polish wake word retraining** — openWakeWord's synthetic training set
  needs Piper-generated Polish utterances (50+ syntheses + 5 real ones).
  Recipe lives in `scripts/train-wake-word.py` (M6).
- **TTS prosody on technical terms** — Piper Polish voices stumble on
  loanwords (SSH, Wi-Fi, ASR). We pre-substitute "es-es-ha", "wi-fi",
  "a-es-er" in TTS preprocessing for known terms — see
  `rpi5/src/blazend/tts/pronunciation.py` (M4).

---

## 6. Testing both languages

Test scenarios under `rpi5/tests/scenarios/` are organised by language tag:

| File                                | Language | Tier |
|-------------------------------------|----------|------|
| `01-wake-word.yaml`                 | EN       | 3    |
| `02-basic-commands.yaml`            | EN       | 3    |
| `03-system-control.yaml`            | EN       | 3    |
| `04-conversation.yaml`              | EN       | 3    |
| `05-fail-modes.yaml`                | both     | 3    |
| `06-pl-wake-word.yaml`              | PL       | 3    |
| `07-pl-basic-commands.yaml`         | PL       | 3    |
| `08-pl-conversation.yaml`           | PL       | 3    |
| `09-language-switch.yaml`           | EN↔PL    | 3    |

Each scenario YAML has a `language:` key in metadata; the runner uses it
to pick the right Piper voice when synthesising user audio (we want the
test mic to "sound" Polish for the PL scenarios, not Anglo-Polish).

A Tier 0 invariant test (`rpi5/tests/unit/test_bilingual_coverage.py`) asserts:

1. Every fast-path intent has both `en:` and `pl:` triggers.
2. Every TTS confirmation phrase exists in EN and PL.
3. Both wake-word entries are present and `allowed`.
4. At least one PL scenario exists.

---

## 7. Roadmap impact

The original `docs/10-ROADMAP.md` had multilingual support arriving at M6.
For the bilingual prototype:

- **M3** (ASR): default model becomes `small` (multilingual), not `small.en`.
- **M4** (LLM + TTS): bilingual system prompt and dual Piper voices land in
  the first conversational milestone.
- **M5** (intents): every fast-path intent ships with PL triggers; the
  language pin/unpin commands (`speak Polish` / `słuchaj uważnie`) are
  dispatched and the reply language follows the pin (§3.6).
- **M6** (voice config): adds the WiFi/model voice-mutation flows and the
  custom-PL-wake-word training script. The pivot from "EN only with PL
  on demand" to "PL + EN co-default" makes this milestone smaller, not
  larger.

A separate `docs/14-LOCALISATION.md` will be added when we extend beyond
EN+PL (German is the most-requested next).

---

## 8. Quick reference (PL)

W skrócie:

- `blazen_os` rozumie i mówi **po polsku** od pierwszej iteracji.
- Wake word: *"hej Jessico"* (PL) lub *"hey Jessica"* (EN). Oba aktywne
  jednocześnie.
- Język odpowiedzi dopasowuje się do języka zapytania. Można go też
  przypiąć: *"mów po polsku"* lub *"speak English"*.
- Domyślny LLM (Qwen 2.5 3B Instruct) jest wielojęzyczny — odpowiada po
  polsku swobodnie.
- Domyślny ASR to multilingualny `small` (lepszy dla PL niż `small.en`).
- Polski głos TTS: `pl_PL-darkman-medium` (alternatywnie `pl_PL-gosia-medium`).
- Polskie wymowy literek "ą/ę" w `small` bywają niepewne — przy reklamacji
  użytkownika Jessica może zaproponować przejście na `medium` lub
  `large-v3-turbo`.
