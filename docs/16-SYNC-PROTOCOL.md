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

Anything in this list: **after editing, commit and push.**

> **Sync transport (revised 2026-06-11): git via GitHub, not rsync.**
> The canonical hub is
> `git@github.com:Marysia-Software-Limited/BlazenOS.git` (branch `main`).
> Both **paul** (`~/dev/blazen_os`) and **rachel** (`~/dev/blazen_os`)
> are clones of it. Synchronisation is now `commit` → `push` → the other
> side `pull`s — on **both sides**. The old rsync targets
> (`push-paul`/`pull-paul`) are deprecated and kept only for bulk
> build-artifact transfer (qcow2/img), never for source or docs.

- **Either Claude pushes** → `git add -A && git commit && make sync-push`.
  `make sync-push` runs `make test-fast` first and **refuses to push if
  tests are red or the tree is dirty** (commit before pushing).
- **The other side pulls** → `make sync-pull` (`git pull --ff-only
  origin main`). From paul you can also converge rachel directly with
  `make rachel-pull`.

Anything outside the shared-boundary list is still host-local, but
because both hosts now share one git history, committing it is cheap and
keeps the tree coherent — push it too unless it's pure scratch work.

## 4. Pull cadence

Pull from origin before you start a session whose work touches the
shared boundary:

```
either side: make sync-pull   (git pull --ff-only origin main)
                              before editing docs/product/, schemas, intents
```

You don't need to pull for purely host-local work (e.g., when paul
Claude is debugging a Rust component or rachel Claude is wiring up a
native screen) — but pulling is cheap and avoids a later merge, so
prefer pulling at the start of every session.

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

If both Claude sessions edited the same file before syncing, git — not
mtime — arbitrates:

1. Whoever pushes first wins the fast-forward. The second pusher's
   `git push` is rejected ("non-fast-forward"); they `git pull` (which
   merges or rebases), resolve any conflict markers by hand, re-run
   `make test-fast`, then push.
2. Nothing is silently overwritten — both versions live in git history,
   recoverable via `git reflog` / `git stash`.
3. For `docs/product/` (the shared spec, symlinked into rachel): the
   merge happens in blazen_os; rachel picks it up on its next
   `make sync-pull`.

When in doubt, the convention is:

- `docs/product/` → spec discussion via HANDOFF.md before the second
  edit. Don't race.
- `configs/_schema/events/` → bump the topic schema's `version` so
  the other side notices a contract change.
- Code-only conflicts (host-local) → not a sync issue.

## 8. Daily flow (illustrative)

```
morning (rachel / macOS)   morning (paul)
─────────────────          ─────────────────
make sync-pull             make sync-pull
(native/Dart tests)        make test-fast
(work happens)             (work happens)
(native/Dart tests)        make test-fast
git commit -am '…'         git commit -am '…'
make sync-push             make sync-push
                           git pull picks up rachel's commits, or vice
                           versa; first push wins the fast-forward.

                           (paul makes a paul-only image build that
                            takes 25 min — no sync needed; the build
                            artefact is gitignored and stays host-local.)
```

## 9. PL TL;DR

Każda zmiana w kodzie/configu **MUSI** mieć też update w dokumentacji.
Synchronizacja idzie teraz przez **git** (a nie rsync): wspólny zdalny
to `git@github.com:Marysia-Software-Limited/BlazenOS.git` (gałąź `main`),
a paul i rachel to dwa klony. Po zmianie: `git commit`, potem
`make sync-push` (najpierw odpala `make test-fast` i odmawia push przy
czerwonych testach lub brudnym drzewie). Druga maszyna pobiera zmiany
przez `make sync-pull` (`git pull --ff-only`). Z paula można od razu
zsynchronizować rachel poleceniem `make rachel-pull`. `HANDOFF.md` to
skrzynka odbiorcza — pisz tam co zostawiasz dla drugiej sesji.
Konflikty rozwiązuje git (pierwszy push wygrywa fast-forward; drugi
robi `git pull`, scala ręcznie, ponawia testy, pushuje). Nie wyścigaj
się o ten sam plik bez wcześniejszej notatki w `HANDOFF.md`.
