# 16 — Sync protocol (paul ↔ macOS)

The two Claude sessions (paul = blazen_os, macOS = rachel) work in
parallel. Every change either side makes is **incomplete until** it's
landed in:

1. The **code or config** itself.
2. The **documentation** that describes it.
3. The **other host**, if the change crossed the shared boundary.

This doc is the rule book.

## 1. The "every change goes everywhere" rule

> **Rule (2026-06-11):** Every change you make as Claude must include,
> in the same logical step:
> - the code/config edit,
> - the docs that describe it,
> - a sync to the other host **if** the change crosses the shared
>   boundary (see §3).

If you can't update docs in the same turn (work in progress, multi-step
plan), use `TaskCreate` to remember it — and write the open task into
`HANDOFF.md` for the other Claude session.

## 2. What counts as "the docs"

Every change touches at least one of these:

| Area touched              | Doc to update                                |
|---------------------------|-----------------------------------------------|
| `configs/intents/`        | `docs/04-VOICE-PIPELINE.md`, `docs/07-CONFIGURATION.md`, scenario in `tests/scenarios/`, and `docs/product/04-CONVERSATION-MODES.md` |
| `configs/wake-word.yaml`  | `docs/05-MODELS.md`, `docs/product/02-PERSONA-AND-WAKE.md` |
| `configs/voice-policy.yaml`| `docs/07-CONFIGURATION.md` |
| `configs/fabric.yaml`     | `docs/product/11-FABRIC.md`, `docs/product/13-RESOURCE-SHARING.md` |
| `configs/_schema/events/*`| `docs/01-ARCHITECTURE.md` §IPC; `rachel/lib/fabric/` Dart types |
| New Rust crate            | `docs/03-SOFTWARE-STACK.md` component table |
| New Python `blazend-*` module | same |
| New flutter file (`rachel/lib/`) | `rachel/docs/platform-mobile/01-ARCHITECTURE.md` if it's a new layer |
| New Flutter test          | `rachel/docs/platform-mobile/08-TESTING.md` if new tier |
| New native plugin (iOS/Android) | `rachel/docs/platform-mobile/07-NATIVE-PLUGINS.md` |
| Roadmap status change     | `docs/10-ROADMAP.md` |
| Operational footgun       | `docs/10-ROADMAP.md` §"M1 operational footguns" |

The pattern is: **find the closest existing doc; update it.** If
there isn't one, write a new doc and link it from `docs/00-INDEX.md`
(blazen_os) or `rachel/docs/platform-mobile/00-INDEX.md`.

## 3. When a change crosses the shared boundary

Shared boundary surfaces:

| Surface                      | Lives in     | Touches macOS Claude? | Touches paul Claude? |
|------------------------------|--------------|:---------------------:|:--------------------:|
| `docs/product/*.md`          | blazen_os    | ✓ (via symlink)       | ✓                    |
| `configs/intents/system.yaml`| blazen_os    | ✓ (rachel reads via pubspec asset) | ✓        |
| `configs/voice-policy.yaml`  | blazen_os    | ✓                     | ✓                    |
| `configs/_schema/events/*`   | blazen_os    | ✓                     | ✓                    |
| `HANDOFF.md`                 | blazen_os    | ✓                     | ✓                    |

Anything in this list: **after editing, push.**

- **macOS Claude push** → `make push-paul` (rsync to paul).
- **paul Claude push** → `make push-mac` (rsync the other way).
- Each `make` target also runs `flutter test` / `pytest` locally
  first, refusing to push if tests are red.

Anything outside this list is host-local — sync at your leisure (or
not at all if it's purely scratch work).

## 4. Pull cadence

Pull from the other host before you start a session whose work
touches the shared boundary:

```
macOS Claude: make pull-paul  (before editing docs/product/, schemas, intents)
paul Claude:  make pull-mac   (same)
```

You don't need to pull for purely host-local work (e.g., when paul
Claude is debugging a Rust component or macOS Claude is wiring up a
Flutter screen).

## 5. Commit-message hygiene

Each change should mention whether it crossed the boundary.

Examples:

```
feat(fabric): add SyncMergeOutcome.resolvedNewer test
                                                # host-local; no push needed

docs(product): clarify wake-word retraining flow
[crossed-boundary]                              # MUST push to other host

feat(asr): switch default to medium multilingual
[crossed-boundary]                              # touches configs/asr.yaml
                                                # AND docs/05-MODELS.md
                                                # AND docs/product/02-PERSONA-AND-WAKE.md
```

The `[crossed-boundary]` tag makes it grep-able when the other
session catches up.

## 6. What goes in HANDOFF.md

`HANDOFF.md` is the **inbox** the other Claude reads first. Update it
when you:

- Leave open work the other session needs to finish.
- Discover a footgun or quirk that wasn't obvious.
- Hit a "this is paul's job" (or "this is macOS's job") boundary.
- Land a milestone that the other session should respond to.

Keep entries chronological at the top, prune older ones once they
become obvious from `docs/`.

## 7. Conflict resolution

If both Claude sessions edited the same file before syncing:

1. Whoever pulls second sees both versions (rsync would silently
   overwrite — we use `--update` flag in `push-*` Make targets so
   newer wins by mtime).
2. The losing edit is recovered from the host's local git stash.
   Both hosts keep working git repos; conflicts are merged by hand.
3. For docs/product/ (the symlinked shared spec): the merge happens
   in blazen_os only; macOS sees it on next pull.

When in doubt, the convention is:

- `docs/product/` → spec discussion via HANDOFF.md before the second
  edit. Don't race.
- `configs/_schema/events/` → bump the topic schema's `version` so
  the other side notices a contract change.
- Code-only conflicts (host-local) → not a sync issue.

## 8. Daily flow (illustrative)

```
morning (macOS)            morning (paul)
─────────────────          ─────────────────
make pull-paul             make pull-mac
flutter test               make test-fast
(work happens)             (work happens)
flutter test               make test-fast
make push-paul             make push-mac
                            
                            (paul makes a paul-only image build that
                             takes 25 min — no sync needed; macOS
                             keeps working unaffected.)
```

## 9. PL TL;DR

Każda zmiana w kodzie/configu **MUSI** mieć też update w dokumentacji.
Jeśli zmiana dotyka wspólnej powierzchni (lista w §3), trzeba ją
zsynchronizować na drugą maszynę przez `make push-paul` (macOS) lub
`make push-mac` (paul). Druga sesja Claude pobiera zmiany przez
`make pull-*`. `HANDOFF.md` to skrzynka odbiorcza — pisz tam co
zostawiasz dla drugiej sesji. Konflikty rozwiązuje się przez git stash
i ręczny merge; nie wyścigaj się o ten sam plik bez wcześniejszej
notatki w `HANDOFF.md`.
