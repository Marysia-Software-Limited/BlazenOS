# 07 — Daily briefing

Jessica's "good morning". A 60-90 s spoken summary triggered on the
**first wake-word of the day after a configurable hour**. The user
can also explicitly trigger it with "Jess, briefing" / "Jess,
streszczenie dnia".

## Configuration

```yaml
# configs/briefing.yaml
version: 1
window:
  earliest_hour: 6        # local time
  latest_hour: 10
  weekday_offset: { sat: 1, sun: 1.5 }  # later on weekends
greeting:
  use_user_name: true
  use_weather: true                     # tomorrow's forecast included
  use_date: true
sections:
  - calendar
  - alarms
  - weather
  - email_highlights
  - messenger_highlights
  - news_local
  - news_national
  - news_world
  - facebook_curated
  - reminders_today
voices:
  warmup_phrase_pl: "Dzień dobry"
  warmup_phrase_en: "Good morning"
sections_max_seconds:
  calendar: 15
  alarms: 5
  email_highlights: 15
  messenger_highlights: 10
  news_local: 10
  news_national: 10
  news_world: 10
  facebook_curated: 10
  reminders_today: 5
  weather: 8
```

## Flow

```
wake → user is detected as primary → today_briefing_due() ?
    │
    └── if true:
         1.  "{greeting}, {name}." {date}. {weather summary}.
         2.  For each section in `sections`:
                 a.  Build content using the right adapter.
                 b.  TTS within section's max-seconds budget.
                 c.  Listen for "skip"/"pomiń" / "stop"/"cicho"
                     between sections — honour immediately.
         3.  "{closing tag}." e.g. "miłego dnia."
         4.  Mark briefing_completed=today.
```

If the user says "stop" mid-section, Jessica:

- Stops talking within 250 ms.
- Marks the briefing as half-done; doesn't replay unless asked.
- "Reszta?" / "Reszta briefingu?" continues.

## Section formats

### Calendar
> "Masz trzy wydarzenia: o 10 stand-up z zespołem, o 13 obiad z
> Beatą, o 16 dentysta."

### Alarms
> "Jeden alarm — o 12:30, na lekarstwo."

### Weather (mobile only by default; appliance opt-in via location)
> "Dziś rano 8 stopni, w południe 14, słonecznie — wieczorem deszcz."

### Email highlights
Up to 3 senders. Spam filtered. Threaded — newest message per thread.
> "Trzy nowe maile. Jeden ważny od Adama o ofercie. Dwa pozostałe — od
> banku i Allegro. Powiedz: ‘czytaj Adam’ — przeczytam pierwszy."

### Messenger highlights
> "Dwa wątki z nowymi wiadomościami. Beata — o weekendowych planach.
> Klub jogi — wciąż gadają o nowej sali. Chcesz przeczytać?"

### News
Per category. Each item: ≤ 12 s spoken. Source attribution every
section (so the user knows where it came from).
> "Lokalne: w Warszawie remont mostu Łazienkowskiego — ruch jednym
> pasem. Krajowe: posiedzenie Sejmu o budżecie. Świat: spotkanie G7 w
> Vancouver."

### Facebook curated
Up to 3 posts from curated friends.
> "Z Facebooka — Adam dodał zdjęcie z gór. Magda pisze o nowej książce.
> Skomentować coś?"

### Reminders today
> "Na dziś masz dwie notatki: kup mleko, zadzwoń do mamy. Mam je
> przypomnieć później?"

## Personalisation

The user can mute sections or change their order at any time:

```
"Jess, nie czytaj mi rano Facebooka"      → sections.remove('facebook_curated')
"Jess, najpierw newsy lokalne"             → reorder
"Jess, krócej z emailami"                  → sections_max_seconds.email_highlights = 8
```

Settings stored in `voice-overrides.yaml`.

## Multi-user

When voice ID resolves a non-primary user during briefing window,
Jessica:

1. Skips email/messenger/calendar/FB by default (they're primary-only).
2. Still does weather + news + alarms (public).
3. Says: "Dzień dobry, {name}. Beata jeszcze nie słuchała briefingu —
   jak będziesz w pobliżu, daj jej znać."

## Failure modes

| Failure                       | What happens                                  |
|-------------------------------|-----------------------------------------------|
| No network                    | Skips news + FB; reads calendar + reminders + alarms only. Says "wiadomości jak będzie internet." |
| Email auth expired            | Says "muszę się przelogować do poczty" — skips email; rest continues. |
| Source returns 0 items        | Section quietly skipped (Jessica doesn't say "no news"). |
| All sections empty            | Single line: "Dzień dobry. Dziś żadnych nowych spraw — można na luzie." |

## Cross-implementation parity

Both `blazen_os` and `rachel` produce **the same content** for the
same configuration. Differences:

- **Trigger detection:** appliance uses wake-word + sunrise-ish clock;
  mobile uses iOS BackgroundTasks / Android WorkManager + lock-screen
  notification ("touch the bell to hear today's briefing").
- **TTS voice:** appliance uses Piper PL voice; mobile uses
  `AVSpeechSynthesizer` (iOS) / Android TextToSpeech with the closest
  PL voice match.
- **Audio output target:** appliance to speakers; mobile follows the
  current output route (built-in speaker, AirPods, car CarPlay).

The shape of the briefing — order, length, what's said — is fully
determined by `configs/briefing.yaml` shared across both.
