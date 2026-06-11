# 03 — User stories

Every story has the same shape:

> **As** {user persona} **I want to** {action} **so that** {outcome}.

Each story tags **A** (appliance / `blazen_os`), **M** (mobile / `rachel`),
or **A+M**. M-only stories are usually motion/location aware; A-only
stories are usually multi-person or always-on.

The list is canonical. Adding a feature without first writing the user
story is a smell — start in the brainstorming skill.

---

## US-01 — Wake and respond by name **(A+M)**

> **As a** primary user, **I want to** say "Jess" or "hey Jessica"
> from across the room **so that** Jessica acknowledges and waits
> for my next sentence without me touching anything.

Acceptance:
- The acknowledgement lands within 300 ms of the end of the wake
  phrase (appliance) / 400 ms (mobile foreground; 700 ms in lock-screen
  mode).
- The reply language matches the wake-word language hint.

## US-02 — Ask an open-ended question **(A+M)**

> **As a** primary user, **I want to** ask Jessica anything ("ile
> kosztował dolar przed wojną w 1914?") **so that** she replies in
> one or two natural-sounding sentences, sources spoken on demand.

Acceptance:
- Cold answer in ≤ 4 s on appliance with cloud fallback, ≤ 6 s on
  mobile cellular.
- "Source?" / "skąd to wiesz?" follow-up surfaces a brief citation.

## US-03 — Deep research a question **(A+M)**

> **As a** user with a hard or compound question ("compare the carbon
> impact of a Pi 5 cluster vs. a single big workstation for 5 years
> of dev work"), **I want to** ask Jessica to "go deep on this" / "zrób
> deep research" **so that** she runs Gemini Deep Research and reads
> me back the key findings + a stored full report I can revisit
> visually on mobile or via SSH on the appliance.

Acceptance:
- Jessica announces "this will take a few minutes" + the expected
  cost (free tier / paid tier marker).
- Report stored at `~/jessica/research/<topic>-<date>.md` on Pi /
  Files on iOS / Documents/jessica/ on Android.
- "Open the report" on mobile foregrounds the markdown viewer.

## US-04 — Read me this web page **(A+M)**

> **As a** user, **I want to** say "Jess, przeczytaj mi tę stronę"
> after copying a URL **so that** Jessica fetches it, strips chrome,
> and reads the article aloud or summarises if I say "skróć to do
> trzech zdań".

Acceptance:
- URL source: clipboard, last shared URL, or dictated.
- Plays within 2 s of the request.
- "Skip the intro" / "skróć" skips to the meat.

## US-05 — Hear and reply to an email **(A+M)**

> **As a** user, **I want to** hear new email summaries on demand
> ("masz nowy mail?") and dictate replies **so that** I can clear my
> inbox while cooking.

Acceptance:
- New-mail notification can be voiced (default off — too noisy).
- "Read mail #2" plays sender + subject + body; "skip" / "pomiń"
  advances.
- "Reply: [...]" enters dictation; Jessica reads back the draft;
  "send" / "wyślij" requires a `confirm: loud` per voice-policy.

## US-06 — Triage Facebook Messenger **(A+M)**

> **As a** user, **I want to** hear who messaged me on FB Messenger
> and dictate replies **so that** I can stay in touch without picking
> up the phone.

Acceptance:
- Surfaces by thread, newest first.
- "Reply: [...]" + confirm-soft (per voice-policy: `confirm: single`).
- Read receipts honour Messenger settings (off by default).

## US-07 — Read my Facebook feed **(A+M)**

> **As a** user, **I want to** hear top posts from a curated friends
> list **so that** I get a personal pulse without scrolling.

Acceptance:
- Curated list lives in user state, not on Facebook.
- "Read posts from Adam" / "od Adama" narrows the feed.
- Comment via dictation with `confirm: single` per post; comment text
  is read back before posting.

## US-08 — Take a voice note **(A+M)**

> **As a** user, **I want to** say "Jess, zapisz: kup mleko jutro
> rano" **so that** Jessica stores the note, transcribes it, and can
> recall it on demand or remind me by time.

Acceptance:
- Note storage: SQLite on the device (Pi: `/var/lib/blazen/notes.db`,
  mobile: app-private store).
- "Co miałam zrobić jutro?" / "what was on my list for tomorrow?"
  returns matching notes.
- Notes never leave the device unless user opts into iCloud / Drive
  sync.

## US-09 — Remember and remind me of events **(A+M)**

> **As a** user, **I want to** say "Jess, przypomnij mi w piątek o
> 14 o dentyście" **so that** Jessica creates an event and announces
> it at the right time.

Acceptance:
- Reminders survive a reboot (appliance) / app cold-start (mobile).
- Daily briefing surfaces same-day events first thing in the morning.
- Mobile-only: location-based reminders ("przypomnij mi, jak będę w
  Lidlu, że mam kupić śmietanę").

## US-10 — Morning briefing **(A+M)**

> **As a** user, **I want to** wake up to a 60-90s briefing
> covering calendar, alarms, top notifications, top messenger
> threads, and curated news + Facebook posts **so that** I start
> the day informed.

Acceptance:
- Triggered on first wake-word after a configurable hour
  (default 06:30 local; user-tunable).
- Order: greeting + day + weather → calendar → alarms → email
  highlights → messenger highlights → news (local / national / world)
  → Facebook from curated friends.
- "Skip to news" / "od razu wiadomości" jumps a section.

## US-11 — Play a podcast **(A+M)**

> **As a** user, **I want to** say "Jess, włącz Radio Nowy Świat" /
> "play the latest Lex Fridman episode" **so that** the right thing
> starts playing.

Acceptance:
- Searches podcast index / radio directory / audiobook library.
- Resumes where left off.
- "Pause" / "stop" / "next" / "back 30" honoured during playback.

## US-12 — Play an audiobook **(A+M)**

> Same as US-11 but bookmark-aware. Last position persists per book.

## US-13 — Search radio stations by genre or country **(A+M)**

> **As a** user, **I want to** say "Jess, jakieś polskie radio jazz"
> **so that** Jessica picks a station and starts playing.

## US-14 — Learn my voice **(A+M)**

> **As a** primary user, **I want to** train Jessica to recognise me
> specifically **so that** wake-word false positives drop and "this
> is me, do the destructive thing" voice-confirm becomes possible.

Acceptance:
- 10-prompt onboarding; documented in
  [`06-VOICE-LEARNING.md`](06-VOICE-LEARNING.md).
- Per-user wake threshold is at least 1.3× tighter than the global
  one.
- Optional: hands-free destructive confirm when voice matches the
  primary user's profile AND the active utterance contains the
  confirm phrase ("potwierdzam" / "apply change").

## US-15 — Add a friend to the curated FB feed **(A+M)**

> **As a** user, **I want to** say "Jess, dodaj Adama do moich
> ulubionych znajomych z Facebooka" **so that** his posts surface in
> the daily briefing and on-demand reads.

## US-16 — Mark something seen / done **(A+M)**

> **As a** user, **I want to** say "Jess, mam to" / "got it" after
> she announces an event or alarm **so that** she doesn't repeat it.

## US-17 — Switch language mid-conversation **(A+M)**

> **As a** bilingual user, **I want to** mix EN and PL freely **so
> that** I never have to think about which language Jessica is in.

Acceptance: auto-detect per utterance; pin/unpin via "speak Polish"
/ "mów po polsku" / "słuchaj uważnie" per
[`docs/13-LANGUAGES.md`](../13-LANGUAGES.md).

## US-18 — Run in the kitchen, mobile in pocket **(A+M)**

> **As a** household, **I want to** have the appliance in the kitchen
> and the phone in pocket without conflicts.

Acceptance:
- When both hear the wake at the same time, the one closer to the
  user wins (loudness comparison on the appliance + phone via mDNS
  handshake on a shared LAN).
- The "winner" handles the request; the other goes back to passive.

## US-19 — Stop talking immediately **(A+M)**

> **As a** user interrupted by Jessica reading something I no longer
> need, **I want to** say "stop", "cicho", "wystarczy" **so that** she
> stops within 250 ms.

## US-20 — Send a question to my partner **(A+M)**

> **As a** user, **I want to** say "Jess, napisz do Beaty: kiedy
> wracasz?" **so that** Jessica drafts and (after confirm) sends the
> message via the configured channel (SMS, iMessage, Messenger,
> Signal — first available).

## US-21 — Privacy quick-check **(A+M)**

> **As a** privacy-conscious user, **I want to** say "Jess, co o mnie
> wiesz?" **so that** she lists the stored profile + the categories
> of cloud calls in the last hour.

Acceptance: surfaces the on-device profile and the per-intent cloud
audit log per [`08-PRIVACY-AND-CLOUD.md`](08-PRIVACY-AND-CLOUD.md).

## US-22 — Cloud kill-switch **(A+M)**

> **As a** user in a sensitive context, **I want to** say "Jess, nie
> dzwoń do Google przez godzinę" **so that** all cloud routing stops
> and Jessica replies only with what fits on-device.

Acceptance: returns "ok" + sets a 1h timer on the cloud router; on
expiry says "wracam do trybu z Gemini".

## US-23 — Briefing without surveillance **(A+M)**

> **As a** user, **I want to** disable Facebook in the daily briefing
> **so that** I can keep the briefing while I take a Facebook break.

## US-24 — Per-source mute **(A+M)**

> **As a** user, **I want to** say "Jess, wycisz wiadomości od mojego
> brata" **so that** they don't show in briefing or new-message
> announcements (but I can still ask explicitly).

## US-25 — Mobile-only: location-aware reminder **(M)**

> "Jess, przypomnij mi w Lidlu o śmietanie" → fires when entering a
> geofence around a configured Lidl location.

## US-26 — Mobile-only: in-call helper **(M)**

> "Jess, podsumuj to później" → after the call ends, Jessica
> summarises the conversation (transcript stays on-device).

## US-27 — Mobile-only: AirPods routing **(M)**

> Wake while AirPods are active routes the conversation to the
> earbuds, ducking system audio.

## US-28 — Appliance-only: LED status mirror **(A)**

> The status LED on the ReSpeaker / HAT ring shows green/blue/red per
> [`docs/02-HARDWARE.md`](../02-HARDWARE.md) so the user can glance
> and know Jessica's state without asking.

## US-29 — Appliance-only: physical wake button **(A)**

> A configurable GPIO button is an always-available fallback when
> ambient noise blocks the wake word.

## US-30 — Both: graceful degradation when offline **(A+M)**

> **As a** user on a flaky connection, **I want to** still get
> intent-routed actions (volume, "stop", "what time is it", voice
> notes, alarms) **so that** Jessica isn't useless without internet.

Acceptance:
- Local LLM on-device path handles small talk + clock + notes.
- Cloud-dependent features (Gemini, email, Facebook, podcasts) reply
  "I'd need internet for that — should I try again in a moment?"
- Auto-retries when connectivity is back; user is told.

## US-31 — Fabric: pair phone with Pi **(A+M)**

> **As the** primary user, **I want to** say "Jess, sparuj nowe
> urządzenie" on my Pi and "Jess, dołącz do fabric — kod ..." on my
> phone **so that** the two devices become one Jessica with one
> identity, one voice profile, and one set of notes.

Acceptance: pairing completes in ≤ 60 s; initial sync log replay
≤ 10 s on a LAN; on completion both devices show the other in their
peer list.

## US-32 — Fabric: continue a thread on a different device **(A+M)**

> **As a** user, **I want to** start a conversation at home with the
> Pi and continue it in the car with the phone **so that** Jessica
> remembers what we were talking about.

Acceptance: session-end conversation summary lands in sync log; the
other device surfaces it on next wake with "pamiętasz, o czym
mówiliśmy w domu?".

## US-33 — Fabric: phone shares internet with Pi **(A+M)**

> **As a** user whose home Wi-Fi just dropped, **I want to** say
> "Jess, włącz hotspot dla Pi" on the phone **so that** my Pi keeps
> doing cloud-dependent tasks via the phone's cellular.

Acceptance: phone enables hotspot with a pre-shared SSID/PSK; Pi
auto-joins within 30 s; daily data cap surfaced if exceeded.

## US-34 — Fabric: phone offloads LLM to Pi over LAN **(A+M)**

> **As a** user on the phone with a long question, **I want to** have
> it answered by the Pi's 7B LLM (when on the same LAN) **so that**
> my battery doesn't get drained by mobile LLM inference.

Acceptance: phone detects Pi in fabric; routes long-form replies to
Pi via `fabric.capability.use llm.cpu` or `llm.hailo`; falls back to
on-device LLM if Pi unavailable.

## US-35 — Fabric: Pi routes the answer to AirPods **(A+M)**

> **As a** user with AirPods in ear, **I want to** have Jessica's
> reply play in my ear, even if the wake fired on the Pi **so that**
> I don't disturb the room.

Acceptance: Pi sees `phone.audio.headphones_active` advertised in
fabric state; reply audio is sent as `tts.frame` events via fabric
RPC; phone plays through AirPods; Pi room speaker stays silent.

## US-36 — Fabric: tablet shows the Deep Research report **(A+M)**

> **As a** user, **I want to** ask my Pi for Deep Research, and have
> the resulting report **automatically open on my iPad** when ready
> **so that** I can browse it visually.

Acceptance: Deep Research completion fact broadcast; iPad app
foregrounds the markdown viewer and reads the summary aloud.

## US-37 — Fabric: kick a lost device **(A+M)**

> **As a** user whose phone got stolen, **I want to** say "Jess,
> wyrzuć telefon z fabric" on my Pi **so that** the phone can no
> longer pull or push anything to my fabric.

Acceptance: loud-confirm + (optional) remote wipe ping; sync log
entries from the lost peer's signature rejected from that moment.

## US-38 — Fabric: travel mode (phone-only) **(M)**

> **As a** user away from home for days, **I want to** rely entirely
> on the phone **so that** Jessica's behaviour doesn't degrade when
> the Pi is unreachable.

Acceptance: phone treats stale-Pi as offline after configurable
threshold; local LLM + on-device ML still answer all autonomous
intents; on return, sync log catches up cleanly.

---

## What's intentionally out of scope (for now)

- Calling phone numbers / answering voice calls.
- Smart-home protocol bridging (HomeKit, Matter) — punted to plugin layer
  post-M10.
- Notifications driven by Jessica into Apple Watch / Wear OS faces.
- Buying things on the user's behalf.
- Transcribing voice calls beyond the in-call helper (M only) and only
  with the user's explicit "podsumuj to" trigger.

Out-of-scope items still get bug-reported (so we know there's demand)
but they don't drive implementation.
