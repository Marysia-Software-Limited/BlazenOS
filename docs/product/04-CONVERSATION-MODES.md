# 04 — Conversation modes

Jessica routes every utterance through exactly one mode. The router
runs on-device (regex + small intent classifier) and falls back to
the LLM brain for anything it can't classify.

## 1. Quick Q&A (default)

Free-form question → short answer. Backend chosen by the cloud
policy ([`08-PRIVACY-AND-CLOUD.md`](08-PRIVACY-AND-CLOUD.md)):

| Backend             | When                                       |
|---------------------|--------------------------------------------|
| On-device LLM       | Cloud is disabled, or question is short and conversational. |
| **Gemini Flash**    | Default. Fast (≤ 4 s end-to-end), no Deep Research. |
| Gemini Pro          | Long-form answer requested ("longer please" / "więcej szczegółów"). |

Answer style: **one or two sentences** unless the user asks for more.
A trailing "want sources?" / "podać źródła?" is added when the answer
came from cloud and contains a factual claim.

## 2. Deep Research mode

> Triggered by: "Jess, deep research: ..." / "Jess, zrób analizę: ..."
> / "Jess, sprawdź to dokładnie: ..."

Calls **Gemini Deep Research** (queued, ~3-15 min) and:

1. Announces the expected wait + cost tier.
2. Releases the user — they can keep talking, ask other things, leave
   the room. Background polling waits for completion.
3. On arrival, Jessica announces the report is ready and offers to
   read the executive summary. Full report stored as markdown.

| Storage location                    | Where      |
|-------------------------------------|------------|
| `/var/lib/blazen/research/<topic>.md` | appliance |
| `Documents/jessica/research/<topic>.md` | iOS sandbox + Files |
| `Documents/jessica/research/<topic>.md` | Android scoped storage |

The summary in voice is at most 90 s; the user can ask for sections by
name ("read me the recommendation", "co rekomenduje?").

## 3. Web page reading

> Triggered by: "Jess, przeczytaj mi tę stronę" / "read this page" /
> "Jess, podsumuj tę stronę".

URL source priority:

1. URL spoken explicitly in the utterance.
2. Last URL on the system clipboard (mobile: app gets pasteboard
   notification; appliance: only when the user `ssh`-pastes one in).
3. Last URL Jessica saw via a share-sheet hand-off (mobile) or the
   `/run/blazen/last-url.txt` write hook (appliance).

Pipeline:

```
URL → fetch (HEAD + GET) → Readability/Trafilatura extract main text →
  preprocess (collapse code blocks; spell out symbols for TTS) →
  hand to Q&A or Read-Aloud mode
```

Modes available:

| User phrase                    | Effect                                  |
|--------------------------------|-----------------------------------------|
| "read the page"                | Reads entire main text aloud, sectioned.|
| "summarise"                    | One-paragraph summary + offer to read.  |
| "skróć do {n} zdań"            | N-sentence summary.                     |
| "what are the headings?"       | Reads outline.                          |
| "jump to {section}"            | Skips to section by partial match.      |
| "skip the intro"               | Heuristic — skip first paragraph.       |

The fetched copy is cached on-device for 24h so "read it again" / "od
nowa" doesn't re-hit the network.

## 4. Voice notes

> Triggered by: "Jess, zapisz: ..." / "Jess, notatka: ..." / "make a
> note: ..."

Two storage modes, both per-device by default:

| Mode      | What's stored                                       |
|-----------|-----------------------------------------------------|
| Quick     | Just the transcript + ts; no audio kept.            |
| Full      | Transcript + the 30-s audio window; replayable.     |

Default: Quick. Full kicks in if the user says "zapisz nagranie" /
"keep the recording".

Recall:

| User phrase                            | Effect                          |
|----------------------------------------|----------------------------------|
| "co miałam zrobić jutro"               | Lists notes tagged tomorrow.    |
| "przeczytaj notatkę o ..."             | Finds by content match.         |
| "ile mam notatek"                      | Count summary.                  |
| "usuń ostatnią notatkę"                | Single-confirm delete.          |
| "pokaż notatkę {n}" *(mobile only)*    | Opens the note view on screen.  |

Notes are tagged automatically by simple rules (`@tomorrow`, `@today`,
`@list`, `@phone`, plus dates extracted by spaCy or a regex). The user
can also tag explicitly: "Jess, dodaj tag praca".

## 5. Reminders + events

Same lexical surface as notes, but with a time/place clause:

- "Jess, przypomnij mi w piątek o 14 o dentyście"
- "Jess, przypomnij mi w niedzielę rano o kawie z Adamem"
- mobile only: "Jess, przypomnij mi w Lidlu, że potrzebuję śmietany"

Storage: same DB as notes, separate table.

Surface times:

- 60 minutes before (configurable).
- 5 minutes before.
- At the scheduled time.
- Re-prompted every 10 minutes until the user says "mam to" / "got
  it" or skips (max 6 re-prompts).

## 6. Email triage

Backed by IMAP. M selects from configured accounts (iOS Mail-app
accounts on phone, configured IMAP creds on Pi via SSH-only config).

Triage shape:

```
"masz 3 nowe maile"
  → "z kim chcesz zacząć?"
  → "z Adamem"   |   "od początku"
      → reads sender + subject + body summary (max 30s spoken)
      → "co dalej?"
          → "reply", "delete", "archive", "mark unread", "next"
            (or PL equivalents: odpowiedz, usuń, archiwizuj, ...)
```

Replies are loud-confirmed (per voice-policy `system.email.send`
mapping):

```
user: "odpowiedz: tak, do zobaczenia w piątek"
Jess: "do Adama: ‘tak, do zobaczenia w piątek.’ wysłać?"
user: "potwierdzam"   |   "apply change"
Jess: "wysłano."
```

## 7. Facebook Messenger

Same shape as email — list newest threads, read body, reply with
single-confirm by default (not loud — Messenger replies are lighter
weight than email).

Per-thread mute: "Jess, wycisz wątek z {name}" → suppresses surfacing
in briefing + new-message announcements.

## 8. Facebook feed

A curated list of friends/pages in user state. Jessica only reads
posts from people on that list. The user adds with:

```
user: "Jess, dodaj Adama do moich ulubionych znajomych z Facebooka"
Jess: "który Adam? Adam K. albo Adam M.?"
user: "K"
Jess: "dodałam Adama K. — odtąd będę czytać jego posty."
```

Comments use single-confirm (`confirm: single`) per
`voice-policy.yaml`.

## 9. Media playback (music / podcasts / radio / audiobooks)

> "Jess, włącz Radio Nowy Świat", "play the latest Lex Fridman",
> "puść Władcę Pierścieni od ostatniego miejsca".

Resolution:

1. Active media context (resume) — book/podcast last position wins.
2. Local library (if any indexed) — on appliance via mDNS; on mobile
   via Apple Music / Spotify SDK.
3. Online catalogues — PocketCasts / Audible / RadioBrowser as
   configured.

Voice transport controls (all mode-internal — they don't go through
the brain):

```
pause | resume | stop | next | back 30 | forward 30 |
volume up | volume down | volume to 70 percent |
play this on the kitchen speaker  (multi-room A only)
```

PL equivalents per `configs/intents/system.yaml`.

## 10. Conversation memory

A single rolling context window per active session
(`memory.session_*`). On idle for 5 min the session closes and is
summarised. Stored summaries are queryable ("o czym mówiłyśmy wczoraj
wieczorem?") but not exposed to the cloud LLM unless the user
explicitly says "włącz pamięć" / "share my memory".

The local summary uses the on-device LLM only — never cloud.

## Mode selection — precedence

When a user utterance plausibly matches multiple modes:

```
1. Transport controls (pause/stop/skip) — always win.
2. Wake-only ack ("yes?", "tak?") — no-op, just acknowledges.
3. Explicit mode trigger ("Jess, deep research: ...").
4. Email/messenger triage if the user is already in a triage flow.
5. Note creation if the utterance starts with "zapisz" / "make a
   note".
6. Reminder creation if the utterance contains a time clause.
7. Media playback if a known title/podcast match exists.
8. Fall back to Quick Q&A.
```

Routing rules live in `configs/intents/system.yaml`
(appliance) / `assets/intents/system.yaml` (mobile). Both projects
read this from the same shared YAML structure.
