# 21 — Translations: the message catalogs

*Decision 2026-08-27: every user-facing string lives in a per-surface message
catalog editable by external translators (or agents). Recognition data does
not.* Companion to [`13-LANGUAGES.md`](13-LANGUAGES.md) (the language policy:
Polish-first, EN co-equal **asset**, runtime currently Polish-only).

## Where the strings live

| Surface | Catalog | Runtime API |
|---|---|---|
| Pi 5 appliance / Linux desktop (Python) | [`configs/phrases.yaml`](../configs/phrases.yaml) | `blazend.i18n` — `msg(key, lang, **params)`, `plural(key, n, lang)`, `table(name, key, lang)` |
| Android | `android/app/src/main/res/values/strings.xml` (EN) + `values-pl/strings.xml` (PL) | `stringResource` / `Resources` |
| iOS | `ios/Jessica/Resources/L10n.swift` (→ `.xcstrings` at M1) | `L10n` typed statics |
| Linux node CLI (`jessica`) | `linux/agent/src/jessica_linux/l10n.py` (local — the mesh subcommands run on nodes without blazend) | `jessica_linux.l10n.msg` |
| macOS node (rachel) | `macos/agent/src/rachel/l10n.py` | `rachel.l10n.msg` |
| Rust shared core (`jessica-core`) | inline `t(lang, pl, en)` in `mind.rs` (2 prompt fragments — small enough to stay) | — |

`configs/phrases.yaml` layers like every config: the shipped defaults can be
overridden per machine in the site dir (`/etc/blazen/phrases.yaml` on the
appliance, `~/.config/blazen/phrases.yaml` on desktop installs) — a site can
reword a single message without touching the shipped catalog.

## Rules for translators (human or agent)

1. **Edit language values only** — the text after `pl:` / `en:`. Never rename
   a key, never add/remove/rename a `{placeholder}` (the code passes exactly
   those parameters; Tier 0 `test_i18n_catalog.py` fails on any mismatch).
2. **Every key keeps every language.** Runtime ships Polish-only, but EN is a
   retained asset (config flip re-enables it — see 13-LANGUAGES.md, Decision
   2026-06-27). Adding a key means adding all languages at once. Polish is
   listed first in each entry.
3. **Plural keys** carry per-category forms — Polish `one`/`few`/`many`
   (1 utwór / 2 utwory / 5 utworów), English `one`/`other`. Translate each
   category; `{n}` is the count.
4. **`cues:` are pre-rendered audio.** The XTTS voice cache keys on the exact
   text, so rewording a cue silently invalidates its recording — re-run
   `scripts/render-voicebank.py` after changing any `cues:` (or `prerender:`)
   entry.
5. **`prompts:` steer the LLM**, not the speaker directly. They control the
   language and structure of generated replies — translate faithfully,
   keeping all placeholders and any output-format instructions intact.
6. **Never translate recognition data.** Intent trigger regexes
   (`configs/intents/system.yaml`), radio-station `aliases:`
   (`configs/radio.yaml`) and similar are *matcher inputs* full of regex
   syntax (`(ł|l)`, `?P<value>`) — machine-translating them silently breaks
   command matching. New spoken forms for a language belong there, but they
   are engineering changes with tests, not catalog edits.
7. Many tests and VM scenarios assert on exact output fragments
   ("Włączam stację Trójka.", "zacięło"). Rewording an existing message is a
   code change: run `make test-fast` and fix the paired assertions in the
   same commit — never "just edit the YAML" for shipped wording.

## Adding a whole language (beyond pl/en)

The catalog is the easy half. A usable language also needs: a Piper voice in
`configs/tts.yaml` (`voice_by_language` + `voices:`), a wake model
(`configs/wake-word.yaml`), intent trigger lists for the language
(`configs/intents/system.yaml`), sentence-slicer abbreviations
(`assistant/sentences.py _ABBREV`), number/time words (`assistant/plnum.py`
is Polish-specific; other languages use digits or need their own module),
and `pronunciation_overrides` in `tts.yaml`. `blazend.i18n` itself needs a
plural rule for the language (`_plural_category`).

## Enforcement

- `rpi5/tests/unit/test_i18n_catalog.py` — parity (pl+en on every key),
  placeholder consistency across languages, no recognition-regex leakage,
  Polish-first ordering, API fallback behaviour.
- `rpi5/tests/unit/test_bilingual_coverage.py` — the wider asset-parity
  invariants (voices, wake models, intents, scenarios).
- The pinned-literal unit tests + VM scenarios are the last line: a catalog
  edit that changes runtime wording turns them red.

## History / inventory

Extracted 2026-08-27 from: five duplicated `_t(lang, pl, en)` helpers
(tools/engine/dispatch/recovery/inference), inline `if lang == "pl"` branches,
local `t()` closures in the supervisor, `_HELP_PL/_EN`, `_WMO_PL/_EN` weather
tables, `_NEWS_LABELS`, hand-rolled Polish plural helpers, and hardcoded
PL-only strings in the supervisor, the Linux CLI and rachel (~25 of which had
no EN at all — authored during extraction). Wording was moved **verbatim**;
any rewording after the move is a deliberate, test-covered change.
