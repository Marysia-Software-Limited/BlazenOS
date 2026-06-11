# 11 — Jessica fabric

A user's Jessica is **one identity living on multiple devices**.
The Raspberry Pi appliance, the iPhone, and (later) the Apple Watch
are not three separate assistants — they are three **nodes** of the
same Jessica. They share context, voice learning, and resources, and
they negotiate who handles which request based on who's closest,
fastest, and least busy.

This doc is the architectural contract for the fabric. Pairing details
live in [`12-PAIRING.md`](12-PAIRING.md), resource sharing in
[`13-RESOURCE-SHARING.md`](13-RESOURCE-SHARING.md).

> **Decision (2026-06-11):** Each node is **autonomous** (works fully
> without the others) AND **cooperative** (uses other nodes when it
> would deliver a strictly better answer). No master node, no required
> cloud. The fabric is **eventually consistent** across nodes — never
> blocks the user on a peer being reachable.

## 1. Mental model

```
                                       ┌──────────────┐
                                       │   user U1    │
                                       │  (Jessica)   │
                                       └──────┬───────┘
              ─── one identity, many nodes ───┤
                                              │
        ┌──────────────────┬──────────────────┼──────────────────┬─────────┐
        ▼                  ▼                  ▼                  ▼         ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  …
│  blazen_os   │  │   rachel     │  │   rachel     │  │  watchOS app │
│  (Pi 5)      │  │  (iPhone)    │  │  (iPad)      │  │  (M11+)      │
│  kitchen     │  │  pocket      │  │  desk        │  │  wrist       │
└──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘
        │                  │                  │                  │
        └──────────── shared fabric state (CRDT log) ─────────────┘
        │                  │                  │                  │
        └──────────── resource offers + RPCs ──────────────────────┘
```

The state-sharing layer and the resource-sharing layer are
**independent** — a node can sync state without offering resources, or
offer a TTS resource without exposing its notes.

## 2. What's shared

| Class                   | Shared? | How       | Why                                              |
|-------------------------|:-------:|-----------|--------------------------------------------------|
| User identity (one name, languages) | ✓ | sync log | A single Jessica means a single user. |
| Voice ID embedding (Layer 2 in [`06`](06-VOICE-LEARNING.md)) | ✓ | sync log | "Same primary user everywhere." |
| Wake-word fine-tuned model | ✓ | sync log + model blob CDN within fabric | Train once, use everywhere. |
| Conversation memory summaries | ✓ | sync log | Continue a thread when you switch device. |
| Notes + reminders + events | ✓ | sync log | One list across devices. |
| Curated FB friends list   | ✓ | sync log | Briefing matches everywhere. |
| Voice-policy + cloud policy | ✓ | sync log | Kill-switches affect all nodes. |
| OAuth tokens / secrets   | ✗ | each node holds its own | Tokens are bound to the device that authorised. |
| Raw audio                | ✗ | never leaves the recording device | See [`08-PRIVACY-AND-CLOUD.md`](08-PRIVACY-AND-CLOUD.md). |
| Per-node cloud audit log | local-first; sync-summary | sync log carries hourly aggregates only | Detailed audit stays on the originating device. |
| Per-node ML models       | offered as RPC, not replicated | RPC | The Pi's LLM is huge; we don't ship it to the phone. |

## 3. Topology

The fabric has no master. Every node knows the **fabric ID** (a
random UUID created at first pairing) and holds a list of peers.

Connectivity (preferred order):

1. **Same LAN** — mDNS discovery (`_jessica._tcp`), TLS over TCP.
2. **Same private overlay** — Tailscale / WireGuard if the user
   already has one; we don't manage it but we use it.
3. **Cellular hot-spot** — when the phone is the LAN's gateway and the
   Pi is connected through it (see
   [`13-RESOURCE-SHARING.md`](13-RESOURCE-SHARING.md) §1).
4. **Optional fabric relay** (opt-in M6) — a single small relay run by
   the user (could be on `paul`'s LAN or a $5/mo VPS) for when the
   nodes are on different LANs and the user wants Jessica continuity
   while travelling.

The relay is an **opt-in** late-milestone feature — without it the
fabric still works perfectly when nodes are on the same network or
share the user's Tailscale.

## 4. Pairing summary

(Full flow in [`12-PAIRING.md`](12-PAIRING.md).)

1. A new device announces "I want to join fabric *X*".
2. An existing device shows a QR (or voice-spelled 6-word code) and
   says "Powiedz Jessice na telefonie: dołącz do mnie".
3. The new device confirms via the same code.
4. After mutual confirmation, identity keys are exchanged over a
   pre-authenticated channel and the new device receives the fabric
   symmetric key.
5. The new device's voice ID embedding and onboarding profile are
   synced from the fabric.
6. New device is online; first sync log replay completes within ~10 s
   on a modest LAN.

## 5. Sync log

A per-fabric **append-only log** of facts. Each entry:

```jsonc
{
  "v": 1,                       // protocol version
  "fact_id": "01HZRG8K2N…",     // ULID
  "fact_type": "note.created",  // see catalogue below
  "ts_ms": 1718093132000,
  "origin": "device-pi-kitchen",
  "lamport": [4, 17, 0, 0],     // vector clock; one slot per node
  "data": { /* fact-specific payload */ },
  "signature": "ed25519:…"      // origin's identity key
}
```

Stored in a SQLite-backed log on every node. New facts are pushed
real-time to live peers; offline peers fetch on reconnect. Conflict
resolution is **last-writer-wins on the same fact_id** with vector
clocks resolving causality (rare in practice — most facts have
distinct IDs).

### Fact catalogue (M1 contract)

| `fact_type`               | Payload sample                                  |
|---------------------------|--------------------------------------------------|
| `identity.user_name`      | `{ name: "Beret", pl_vocative: "Beret" }`        |
| `voice_id.embedding`      | `{ vec: [256 floats], hash: "...", from_node: ... }` |
| `wake.threshold_per_user` | `{ user_id, threshold: 0.55 }`                  |
| `wake.model_blob`         | `{ blob_id: "...", sha256, target: "jessica_pl", size_kb: 4096 }` |
| `note.created`            | `{ note_id, body, lang, tags }`                 |
| `note.deleted`            | `{ note_id }`                                    |
| `reminder.created`        | `{ reminder_id, due_at_ms, body, lang }`        |
| `reminder.acked`          | `{ reminder_id }`                                |
| `curated_friend.added`    | `{ friend_id, name, source }`                    |
| `voice_policy.updated`    | `{ patch: { ... } }`                              |
| `cloud_policy.updated`    | `{ patch: { ... } }`                              |
| `briefing.config_updated` | `{ patch: { ... } }`                              |
| `mute_list.updated`       | `{ added: [...], removed: [...] }`               |
| `conversation.summary`    | `{ session_id, started_at_ms, ended_at_ms, summary, lang }` |
| `fabric.peer_introduced`  | `{ peer_id, peer_pubkey, peer_name, capabilities }` |
| `fabric.peer_removed`     | `{ peer_id }`                                    |

Adding a new `fact_type` requires bumping protocol minor version and
landing in both implementations in the same change set.

## 6. Wake arbitration

When wake fires on multiple nodes at the same time, only one node
handles the request. Steps:

1. Each node that detected the wake broadcasts a `fabric.wake.candidate`
   message: `{ rms_db_avg, snr_db, user_motion_state }`.
2. After a 150 ms window, every node computes the same scoring
   function over candidates:
   - +20 for highest RMS (closest mic)
   - +10 for highest SNR
   - +5 for "user is here" sensor (mobile only — accelerometer activity)
   - +5 for "I have headphones connected" (mobile — likely in pocket)
   - -10 for "I'm currently playing media" (avoid interrupt)
3. The node with the highest score handles the request; others post
   `fabric.wake.deferred` and go quiet.
4. The winner streams `asr.partial` + `brain.reply` + `tts.frame` to
   itself; peers do not see audio events (those stay local).
5. After the response completes, the winner posts
   `conversation.summary` to the sync log so all peers know what
   happened.

If only one node detects the wake (likely the common case), it just
handles it — no arbitration overhead.

## 7. Resource sharing (preview)

Full details in [`13-RESOURCE-SHARING.md`](13-RESOURCE-SHARING.md).
Headline: every node advertises capabilities it's willing to offer
to its peers, and peers can call them via RPC over the fabric
channel.

| Node     | Likely offers                                  | Likely consumes                                 |
|----------|-------------------------------------------------|--------------------------------------------------|
| Pi       | LLM (CPU 3B / Hailo 7B), TTS Piper, large notes DB, ReSpeaker mic | cellular internet (from phone), GPS, calendar access (when no CalDAV on Pi) |
| iPhone   | Apple Intelligence on-device LLM (when phone idle), Apple Speech ASR, GPS, contacts/calendar, cellular tethering | Pi LLM for heavy queries, Pi room speaker, Pi Wi-Fi when phone is on airplane mode |
| Pixel    | Gemini Nano, Google Speech, GPS, calendar, cellular tethering | Pi LLM, Pi room speaker, Pi Wi-Fi |
| iPad     | Larger display for reports / briefing transcripts | same as iPhone |

Each offer + consume is **gated by user-configurable policy** —
sharing is on by default within a paired fabric but every category
can be turned off individually.

## 8. Privacy implications

- The sync log carries **profiles, embeddings, notes, summaries** —
  it is **end-to-end encrypted** (fabric symmetric key) so any relay
  the user might use is opaque to it.
- A device can **leave** the fabric. On leave it deletes the symmetric
  key, drops the local sync DB, and announces `fabric.peer_removed`
  so peers stop accepting facts from it.
- Voice samples **never** cross the fabric. Embeddings (which are
  derived) can, because the spec already treats voice ID as a comfort
  feature and not as auth (see
  [`06-VOICE-LEARNING.md`](06-VOICE-LEARNING.md) §"Why we're
  conservative").
- Cloud kill-switch: a `cloud_policy.cloud_enabled = false` sync log
  fact takes effect everywhere within seconds. Same for quiet hours.
- A "Jess, kick the iPad" voice command requires loud-confirm.
- Pairing requires explicit consent from BOTH ends.

## 9. Failure modes

| Failure                                    | What happens                                                |
|--------------------------------------------|--------------------------------------------------------------|
| One node drops off the LAN                 | Peers mark it `offline`; sync log entries from it stop arriving; local queues accumulate; on reconnect they replay. |
| Two nodes both think they're handling a wake | Arbitration window guards this — extremely unlikely after the 150 ms decision. If it happens once, the late-loser detects and silences itself. |
| Sync log diverges (two devices added the same note off-LAN) | CRDT/vector-clock merge — both notes are kept; UI flags them as "merged from {peer}". |
| New device joins with stale clock          | Fabric requires NTP-aligned clocks within 60 s; we warn the user otherwise. |
| Identity key compromised on one device     | User runs "Jess, wyloguj telefon z fabric" → loud-confirm → key revoked on all peers; lost device can no longer push facts. |
| Relay (M6+) goes offline                   | Nodes on the same LAN keep working unmodified. Off-LAN nodes can't reach each other until relay or LAN return. |

## 10. Cross-implementation contract

- The wire format is the **same** framed JSON envelope used internally
  (`{ v, ts_ms, source, topic, data }`), with TLS-over-TCP transport
  instead of Unix sockets. See `blazen_os/docs/01-ARCHITECTURE.md` §"IPC".
- Topic names live under `fabric.*`.
- The Rust crate `blazend-fabric` and the Dart module `lib/fabric/`
  share the schemas in `configs/_schema/events/fabric.*.schema.json`.
- A reference implementation of the sync log in both languages is
  the **acceptance gate** before M5 — they must round-trip every
  fact_type.

## 11. PL TL;DR

Jessica nie jest osobnym asystentem na Pi i osobnym w telefonie —
to ten sam asystent na obu urządzeniach. **Każde urządzenie działa
samodzielnie** (Jessica na telefonie umie wszystko bez Pi i odwrotnie)
ale **wszystkie urządzenia dzielą jedną pamięć, jeden profil głosowy
i jedne ustawienia**. Notatka zrobiona w kuchni jest widoczna w
telefonie. Przypomnienie ustawione w telefonie zadzwoni też przez
głośnik na Pi. Telefon udostępnia Pi internet z 5G; Pi udostępnia
telefonowi większy lokalny model LLM przez sieć domową; ekran iPada
można wykorzystać do wyświetlenia raportu z Deep Research, który Pi
zamówił. Wszystko jest szyfrowane end-to-end kluczem ustawionym przy
parowaniu, więc nawet jeśli kiedyś włączymy opcjonalny przekaźnik w
chmurze (M6+), nie ma on dostępu do treści.
