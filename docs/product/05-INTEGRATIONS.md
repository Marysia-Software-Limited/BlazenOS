# 05 — Integrations

Each integration documents:

- **API** — the upstream we call.
- **Auth** — how the user grants access.
- **Adapter contract** — the on-device interface every implementation
  exposes.
- **Latency budget** — typical and worst-case.
- **Failure mode** — what Jessica says when the integration is
  unavailable.

The adapter contracts here are the cross-implementation seam —
appliance and mobile share the shape, not the code.

---

## 1. Google Gemini (Q&A + Deep Research)

| Field           | Value                                             |
|-----------------|----------------------------------------------------|
| API             | `https://generativelanguage.googleapis.com/v1beta` |
| Auth            | OAuth (mobile) or API key in `/etc/jessica/secrets/gemini.yaml` (appliance, SSH-only). |
| Models          | `gemini-2.5-flash` (Quick Q&A), `gemini-2.5-pro` (long-form), Deep Research API |
| Latency budget  | Flash: ≤ 2.5 s p95. Pro: ≤ 6 s p95. Deep Research: 3-15 min (async) |
| Failure mode    | "Nie mogę teraz zapytać Google — spróbuję za chwilę." Cache previous answer when applicable. |

### Adapter contract

```ts
interface GeminiAdapter {
  ask(prompt: string, lang: 'en' | 'pl', mode: 'flash' | 'pro'): AsyncIterable<string>;
  deepResearch(prompt: string, lang: 'en' | 'pl'): Promise<{ jobId: string }>;
  pollResearch(jobId: string): Promise<{ status: 'queued' | 'running' | 'done' | 'error'; report?: string }>;
}
```

Implementations:

- `blazen_os/src/blazend/brain/gemini.py`
- `rachel/lib/integrations/gemini.dart`

### Cost ceiling

A per-day token spend cap (default $0.50 USD-equivalent) is enforced
on-device. Hitting the cap → Jessica says "limit wydatków na dziś
osiągnięty — następne zapytania pójdą do lokalnego modelu albo
poczekają do rana." Configurable in `system.cloud.daily_cap_usd`.

---

## 2. Email (IMAP)

| Field           | Value                                             |
|-----------------|----------------------------------------------------|
| Protocol        | IMAP + IMAP IDLE; SMTP for sending                 |
| Providers tested| Gmail (OAuth XOAUTH2), Fastmail, Proton Bridge, generic IMAPS |
| Auth            | Mobile: native Mail.app integration (iOS) / MailIntent (Android). Appliance: SSH-only `/etc/jessica/secrets/email.yaml` |
| Latency budget  | New mail surfaced within 30 s of IDLE notify. Read summary ≤ 1.5 s. |
| Failure mode    | Network drop → "skrzynka nie odpowiada" + retries. Auth fail → "potrzebuję ponownie zalogować się do poczty, zrób to przez ekran/SSH". |

### Adapter contract

```ts
interface EmailAdapter {
  listAccounts(): Promise<{ id: string; address: string }[]>;
  watch(accountId: string): AsyncIterable<EmailHeader>;       // IDLE stream
  fetchUnread(accountId: string, limit: number): Promise<EmailHeader[]>;
  readBody(messageId: string): Promise<{ plain: string; html: string }>;
  draftReply(messageId: string, body: string): Promise<{ draftId: string }>;
  send(draftId: string): Promise<{ ok: boolean }>;
  markRead(messageId: string): Promise<void>;
  archive(messageId: string): Promise<void>;
  delete(messageId: string): Promise<void>;
}
```

Personalised summarisation (sender's history, on-device only) drives
the spoken summary. Default summary length: ≤ 30s spoken (~100 words).

### Mobile-specific

iOS app uses **MessageKit / MailDrop entitlement** (or, when entitlement
not granted, falls back to IMAP via configured account creds). Android
uses **K-9 / FairEmail-style adapter** or generic IMAP creds.

Native Mail app integration is preferred (offers attachments preview,
threading) but is a polish, not a requirement.

---

## 3. Facebook (Messenger + Feed)

| Field           | Value                                             |
|-----------------|----------------------------------------------------|
| API             | Graph API (read), Messenger Cloud API (when allowed); fallback: web scraping with the user's session cookie. |
| Auth            | OAuth via Facebook Login. User explicitly grants `pages_messaging`, `read_user_posts`, `comment_publish` scopes. |
| Latency budget  | New message surfaced within 60 s. Feed refresh on demand: ≤ 4 s for top 20 posts. |
| Failure mode    | API rate limit → "Facebook prosi o przerwę, spróbuj za chwilę." Cookie expired → "muszę się ponownie zalogować, zrób to przez aplikację". |

### Adapter contract

```ts
interface FacebookAdapter {
  watchMessages(): AsyncIterable<FbMessage>;
  listThreads(unreadOnly: boolean, limit: number): Promise<FbThread[]>;
  readThread(threadId: string): Promise<FbThread>;
  replyToThread(threadId: string, body: string): Promise<void>;
  muteThread(threadId: string, until: Date | 'forever'): Promise<void>;

  feedTopFromCurated(friendIds: string[], limit: number): Promise<FbPost[]>;
  commentOnPost(postId: string, body: string): Promise<void>;
  curatedFriends(): Promise<{ id: string; name: string }[]>;
  addCuratedFriend(query: string): Promise<{ matches: FbFriend[] }>;
}
```

### Privacy

Facebook is the most-sensitive integration. Default state:

- Messenger: **on**, requires fresh-cookie session every 30 days.
- Feed: **off** until the user adds at least one curated friend.
- Comments: **off** until the user explicitly says "Jess, mogę
  komentować przez Ciebie" (one-time consent, stored in user state).

---

## 4. News (local / national / world)

| Field           | Value                                             |
|-----------------|----------------------------------------------------|
| Sources         | User-configurable list of RSS / Atom feeds + JSON APIs (BBC, Reuters, Onet, TVN24, Gazeta Wyborcza, RMF24). |
| Auth            | None for public feeds. Optional paid-tier API keys (FT, NYT) in secrets file. |
| Latency budget  | Briefing build: ≤ 3 s. On-demand fetch: ≤ 2 s. |
| Failure mode    | "Wiadomości z {source} są niedostępne — pomijam je w briefingu." |

### Adapter contract

```ts
interface NewsAdapter {
  configure(sources: NewsSource[]): Promise<void>;
  fetchLatest(category: 'local' | 'national' | 'world' | 'tech' | 'science'): Promise<NewsItem[]>;
  summariseItem(item: NewsItem, lang: 'en' | 'pl'): Promise<string>;   // uses Gemini Flash
}
```

Local news inference: starts from the configured user location (Pi:
`/etc/jessica/location.yaml`; mobile: CoreLocation / FusedLocationProvider).

---

## 5. Podcasts, audiobooks, radio

| Field           | Value                                                       |
|-----------------|--------------------------------------------------------------|
| Podcasts        | PocketCasts API (preferred — clean), Apple Podcasts (iOS), iTunes Search (catalog), or generic RSS. |
| Audiobooks      | Audible (when entitled), local DRM-free OPDS catalogue, OverDrive (libraries). |
| Radio           | RadioBrowser (open community DB), TuneIn (when API key configured). |

### Adapter contract

```ts
interface MediaAdapter {
  search(query: string, kind: 'podcast' | 'audiobook' | 'radio'): Promise<MediaResult[]>;
  resume(itemId: string): Promise<void>;
  playFromOffset(itemId: string, offsetSeconds: number): Promise<void>;
  position(itemId: string): Promise<{ offsetSeconds: number; durationSeconds: number }>;
  transport: TransportControls;
}
interface TransportControls {
  pause(): void; resume(): void; stop(): void;
  next(): void; prev(): void;
  seek(deltaSeconds: number): void;
  setVolume(percent: number): void;
}
```

Native playback path:

- **iOS:** `AVPlayer` + `MPNowPlayingInfoCenter` for lock-screen
  controls.
- **Android:** `ExoPlayer` + `MediaSessionService`.
- **Appliance:** `gstreamer` via `blazend-audio-out` (Rust).

---

## 6. Calendar

| Field           | Value                                             |
|-----------------|----------------------------------------------------|
| Sources         | iCloud, Google Calendar, generic CalDAV.          |
| Auth            | Native event store on mobile; CalDAV creds on appliance (SSH-only). |
| Latency budget  | List today's events ≤ 1 s. Add event ≤ 2 s. |

### Adapter contract

```ts
interface CalendarAdapter {
  eventsForDay(date: Date): Promise<CalendarEvent[]>;
  upcoming(within: 'today' | 'this-week'): Promise<CalendarEvent[]>;
  addEvent(e: NewCalendarEvent): Promise<{ eventId: string }>;
  updateEvent(eventId: string, patch: Partial<CalendarEvent>): Promise<void>;
  delete(eventId: string): Promise<void>;
}
```

---

## 7. Reminders / alarms

Stored on-device (DB). Mobile-only: also bridges to iOS Reminders /
Android AlarmManager so the system notification fires even if the
Jessica app isn't running.

---

## 8. SMS / iMessage / Signal / WhatsApp (US-20)

Best-available channel selection per contact. Auth:

- iOS: `MessageUI` + share-extension intent.
- Android: SMS via Telephony API, Signal via Sgnl URL scheme.
- Appliance: out of scope — appliance phones nobody.

---

## Configuration shape

All integrations declare config under `configs/integrations/<name>.yaml`
(shared YAML schema). Both implementations load via the same schema
(`configs/_schema/integrations/<name>.schema.json`).

Sample:

```yaml
# configs/integrations/gemini.yaml
version: 1
api_base: https://generativelanguage.googleapis.com/v1beta
models:
  flash: gemini-2.5-flash
  pro: gemini-2.5-pro
  deep_research: deep-research-1.0
default_lang: pl
daily_cap_usd: 0.50
secrets:
  api_key: file:///etc/jessica/secrets/gemini.yaml#api_key   # SSH-only on Pi; Keychain on iOS; EncryptedSharedPreferences on Android
```

The `file:///...#field` resolution lets both implementations point at
the right storage backend (Pi: secrets file with `0600` perms; iOS:
Keychain; Android: EncryptedSharedPreferences).
