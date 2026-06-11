# Jessica — product specification (shared)

This directory is the **single source of truth** for what the Jessica
assistant does, regardless of which implementation hosts it. Both
[`blazen_os`](../../../blazen_os/) (Raspberry Pi 5 appliance) and
[`rachel`](../../../rachel/) (mobile app, iOS + Android) implement
exactly the surface area documented here.

> **Decision (2026-06-11):** two implementations, one product. New
> features land here first; the implementations follow. A feature that
> only exists in one is a smell — either it's not really part of
> Jessica or the other implementation has a bug.

## Reading order

| # | File | Purpose |
|---|------|---------|
| 00 | [`00-INDEX.md`](00-INDEX.md) | this file |
| 01 | [`01-PRODUCT-OVERVIEW.md`](01-PRODUCT-OVERVIEW.md) | who Jessica is, what she does at a glance |
| 02 | [`02-PERSONA-AND-WAKE.md`](02-PERSONA-AND-WAKE.md) | name handling, wake words EN+PL, casual/formal modes |
| 03 | [`03-USER-STORIES.md`](03-USER-STORIES.md) | the 30+ user stories that drive the design |
| 04 | [`04-CONVERSATION-MODES.md`](04-CONVERSATION-MODES.md) | Q&A, Deep Research, web reading, message reply |
| 05 | [`05-INTEGRATIONS.md`](05-INTEGRATIONS.md) | Gemini, email (IMAP+IMAP-IDLE), Facebook (Messenger+Feed), podcasts, audiobooks, radio, news, calendar |
| 06 | [`06-VOICE-LEARNING.md`](06-VOICE-LEARNING.md) | per-user voice model; how Jessica learns to recognise one speaker reliably |
| 07 | [`07-DAILY-BRIEFING.md`](07-DAILY-BRIEFING.md) | morning greeting flow + content sources |
| 08 | [`08-PRIVACY-AND-CLOUD.md`](08-PRIVACY-AND-CLOUD.md) | what runs on-device, what calls the cloud, what's the audit trail |
| 09 | [`09-MOBILE-PLATFORM-DECISION.md`](09-MOBILE-PLATFORM-DECISION.md) | **Native Swift + Kotlin + shared Rust core** (revised 2026-06-12). Flutter retained as reference impl. |
| 10 | [`10-MOBILE-HARDWARE.md`](10-MOBILE-HARDWARE.md) | reference + supported phones, accessories, fallback hardware |
| 11 | [`11-FABRIC.md`](11-FABRIC.md) | **multi-device Jessica** — one identity across Pi + phone + tablet; sync log + topology |
| 12 | [`12-PAIRING.md`](12-PAIRING.md) | how new devices join the fabric (QR + voice-spelled code) |
| 13 | [`13-RESOURCE-SHARING.md`](13-RESOURCE-SHARING.md) | network, compute, audio, sensor, and storage sharing across nodes |
| 15 | [`15-NATIVE-MIGRATION.md`](15-NATIVE-MIGRATION.md) | **Native migration plan** — concrete steps to move from Flutter (`rachel/`) to `jessica-ios` + `jessica-android` + shared Rust core (2026-06-12). |

## Cross-implementation contract

Both implementations MUST agree on:

1. **Wake word + persona.** Jessica responds to the same names everywhere.
2. **Spoken languages.** English + Polish co-equal (Polish primary for
   the first user).
3. **Intent vocabulary.** Every intent in
   `configs/intents/system.yaml` (blazen_os) has the same name and
   semantics in `lib/intents/system.dart` (rachel).
4. **Integration adapter contract.** Same Gemini prompt structure, same
   email message format, same Facebook accessor shape.
5. **Conversation memory format.** A conversation started on the phone
   can — at minimum — be summarised on the appliance and vice versa
   (M3+ of each project).
6. **Privacy policy.** Both must honour `08-PRIVACY-AND-CLOUD.md`'s
   on-device / opt-in-cloud split.

What is allowed to differ:

- Implementation language, frameworks, package managers.
- Distribution (`apt`/`pi-gen` for the appliance; App Store / Play
  Store for the phone).
- Hardware-specific niceties (LED ring on Pi, haptics on phone).
- ML model choice (on-device Whisper on Pi vs. Apple Speech / Google
  Speech on phones).

## Owning the product

Spec changes go through the canonical brainstorming → spec → plan flow
described in `blazen_os/AGENTS.md` §3. Implementation-specific
follow-ups land in each project's own `docs/` directory, never here.
