# 01 — Product overview

**Jessica** (also "Jess") is a voice-first personal assistant designed to help blind and visually impaired persons. The user talks; Jessica listens, answers, reads things aloud, drafts replies, remembers context, and proactively surfaces what matters today.

She runs in **two skins**:

| Skin | Where | Project |
|------|-------|---------|
| **Appliance** | A Raspberry Pi 5 16 GB on a shelf at home — mic + speaker, no screen. SSH for break-glass admin only. | [`blazen_os`](../../../blazen_os/) |
| **Mobile**    | An iOS or Android phone — voice-first, but the screen is available when useful (e.g., showing the email Jessica just read aloud). | [`rachel`](../../../rachel/) |

## What Jessica does (feature surface)

1. **Wake + name.** Responds to "hey Jessica" / "Jess" / "Jessico" (PL
   vocative). Configurable — see [`02-PERSONA-AND-WAKE.md`](02-PERSONA-AND-WAKE.md).

2. **Open-ended Q&A** via Google Gemini, with an explicit **Deep
   Research** mode for hard or compound questions
   ([`04-CONVERSATION-MODES.md`](04-CONVERSATION-MODES.md) §1-§2).

3. **Web page reading.** "Jess, przeczytaj mi tę stronę" → grabs the
   URL (last-shared / clipboard / dictated), strips chrome via
   Readability, summarises or reads aloud
   ([`04-CONVERSATION-MODES.md`](04-CONVERSATION-MODES.md) §3).

4. **Email.** Hears incoming mail, reads summaries on demand, drafts
   and dictates replies, sends with loud confirmation
   ([`05-INTEGRATIONS.md`](05-INTEGRATIONS.md) §2).

5. **Facebook Messenger.** Reads new messages, allows voice-dictated
   replies, marks read ([`05-INTEGRATIONS.md`](05-INTEGRATIONS.md) §3).

6. **Facebook feed.** Reads top posts from a curated set of
   friends/pages, allows voice-dictated comments
   ([`05-INTEGRATIONS.md`](05-INTEGRATIONS.md) §3).

7. **Voice notes.** "Jess, zapisz: kup mleko jutro rano." Stored,
   searchable, voice-recalled
   ([`04-CONVERSATION-MODES.md`](04-CONVERSATION-MODES.md) §4).

8. **Reminders + events.** Time-based + location-based (mobile only).
   Surfaces matching events during the daily briefing.

9. **Daily briefing.** Morning greeting that names the user, summarises
   today's calendar, surfaces alarms, reads top notifications + email,
   the most-read messenger threads, and curated news (local / national
   / world / Facebook from a configured friend list) — see
   [`07-DAILY-BRIEFING.md`](07-DAILY-BRIEFING.md).

10. **Media playback.** Search, select, and play podcasts, audiobooks,
    and radio stations
    ([`05-INTEGRATIONS.md`](05-INTEGRATIONS.md) §5).

11. **Per-user voice learning.** Jessica learns who's talking to her
    over time. Improves wake-word reliability for the primary user and
    enables "this is me, do the destructive thing"
    confirmations
    ([`06-VOICE-LEARNING.md`](06-VOICE-LEARNING.md)).

## What Jessica is **not**

- A general home automation hub. She can call out to one (via voice
  intent → tool call) but she doesn't replace one.
- A smart-display product. The mobile skin has a screen but is still
  voice-first.
- A music streaming service. She integrates with Spotify / Apple
  Music / PocketCasts / Audible when the user opts in.
- An always-on cloud listener. Wake detection is on-device; cloud
  calls are scoped per intent and announced.

## Why two implementations

A Pi on the shelf gives "always-on, hands-free, low-latency" in a
single room. A phone gives Jessica everywhere — kitchen counter,
commute, walking the dog. Either alone is half the product; both
together is what we ship.

The two **share the spec** documented in this directory. They differ
in implementation per [`AGENTS.md`](../../../blazen_os/AGENTS.md) §1
and the new
[`09-MOBILE-PLATFORM-DECISION.md`](09-MOBILE-PLATFORM-DECISION.md).

## One Jessica, multiple devices — the fabric

A user with both a Pi and a phone has **one Jessica**, not two. The
two skins **pair** into a "Jessica fabric" — a federation of nodes
that share:

- **Identity** (name, languages, persona).
- **Voice ID** (the per-user voice embedding the user trained once).
- **Wake-word fine-tuning** (train once, applies everywhere).
- **Notes, reminders, curated friends, mute lists, briefing config.**
- **Conversation summaries** (continue a thread on a different device).
- **Voice + cloud policy** (kill-switches affect every node).

Each node also **offers resources** to its peers:

- Phone shares cellular / GPS / Apple Speech / Apple Intelligence.
- Pi shares Hailo LLM / room speaker / mic array / bulk storage.
- Either can route requests to whichever has the best resource for
  the task.

This is documented in:

- [`11-FABRIC.md`](11-FABRIC.md) — what's shared, topology, sync log.
- [`12-PAIRING.md`](12-PAIRING.md) — how new devices join.
- [`13-RESOURCE-SHARING.md`](13-RESOURCE-SHARING.md) — the resource catalogue + protocol.

> **Each node remains autonomous.** Pi works perfectly without the
> phone (it always has — that's `blazen_os` standalone). Phone works
> perfectly without the Pi (that's `rachel` standalone). Together
> they are strictly better.

## Quick reference (PL)

Jessica (lub Jess / Jessica) to **głosowy asystent osobisty, stworzony z myślą o osobach niewidomych i słabowidzących**. Działa
w dwóch wcieleniach: jako urządzenie na półce (Raspberry Pi 5 —
projekt `blazen_os`) oraz jako aplikacja mobilna na iOS i Androida
(projekt `rachel`). Oba wcielenia robią to samo: budzą się na imię,
odpowiadają na pytania (Gemini + tryb Deep Research), czytają strony
WWW, maile, Messenger i posty Facebooka, dyktują na nie odpowiedzi,
prowadzą notatki głosowe, pamiętają i przypominają o zdarzeniach,
witają każdego dnia podsumowaniem wiadomości i kalendarza, oraz
odtwarzają podcasty, audiobooki i stacje radiowe. Każde wcielenie
uczy się głosu swojego głównego użytkownika.
