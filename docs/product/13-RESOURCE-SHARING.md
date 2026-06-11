# 13 — Resource sharing across fabric nodes

A Jessica fabric is more than synchronised state — it's also
**pooled hardware**. The phone has cellular and a Neural Engine; the
Pi has a 16 GB RAM LLM and a room-filling speaker; the tablet has a
large screen. Any node can use any of these when it would deliver a
strictly better answer than going it alone.

This doc is the catalogue of what's shared, how it's negotiated, and
how the user controls it.

## 1. Resource categories

### A. Network

| Offering node | What it offers              | Common consumer                                |
|---------------|------------------------------|------------------------------------------------|
| iPhone        | Personal Hotspot (5G)        | Pi when home Wi-Fi drops; Pi as a roving demo. |
| Pixel         | Wi-Fi tethering              | Same as iPhone.                                 |
| Pi (Ethernet) | Wired backhaul + DNS         | Phone when on a flaky cellular link.            |

Default: **off**. Toggled by voice:

| User says (PL / EN)                            | Effect                              |
|------------------------------------------------|--------------------------------------|
| "Jess, włącz hotspot dla Pi" / "share internet with the Pi" | iPhone enables Hotspot + announces SSID/PSK via fabric RPC; Pi auto-connects. |
| "Jess, wyłącz hotspot" / "stop sharing internet" | iPhone disables Hotspot; Pi falls back to home Wi-Fi.       |

Cost-aware:

- Phone tracks Hotspot usage in MB; if it exceeds a configurable cap
  (default 500 MB/day) Jessica warns the user.
- The Pi can be told "Jess, oszczędzaj dane" / "use less data" — it
  switches local LLM mode and stops cloud calls per
  `cloud_policy.cloud_enabled`.

### B. Compute / ML

| Offering node | Capability                         | Common consumer                                                      |
|---------------|-------------------------------------|------------------------------------------------------------------------|
| Pi 5 + Hailo  | LLM 7B-class via Hailo-10H          | Phone when on cellular cap and needs a long answer cheaply.            |
| Pi 5 (CPU)    | LLM 3B Q4 (Qwen 2.5)                | Phone when on-device LLM unavailable (older iPhone / non-Pixel).      |
| iPhone        | Apple Intelligence on-device 3B LLM | Pi as a fallback when Hailo isn't installed and the user wants snappy. |
| Pixel         | Gemini Nano                         | Same as iPhone case.                                                   |
| All           | Speaker ID embedding extractor      | Cross-shared so voice ID is consistent across capture points.         |

Default: **on within the fabric** (we paired explicitly; sharing
across paired devices is the whole point). Per-category opt-out via
`Jess, nie pożyczaj mózgu Pi telefonowi`.

### C. Audio (mic / speaker)

| Offering | Capability                            | Common use                                          |
|----------|----------------------------------------|------------------------------------------------------|
| Pi       | ReSpeaker 4-Mic array + room speaker  | Phone routes "Jess, put on the kitchen speaker" to Pi. |
| iPhone   | AirPods Pro 2 in pocket               | Pi routes "Jess, mów cicho" to phone when user has earbuds in.    |
| iPad     | Audio + 11" display                   | Pi routes "Jess, pokaż mi to" to iPad when nearby.   |

Routing decision per utterance:

1. If the wake came from a node with active headphones, response
   plays on that node by default.
2. Otherwise, the response plays on the node that handled the wake
   arbitration (per
   [`11-FABRIC.md`](11-FABRIC.md) §6).
3. The user can override per command:
   - "Jess, na głośniku" — route to Pi room speaker.
   - "Jess, na słuchawki" — route to AirPods / Buds.
   - "Jess, na ekranie" — route summary text + audio to iPad/iPhone.

### D. Sensors

| Offering | Capability                  | Common use                                            |
|----------|------------------------------|--------------------------------------------------------|
| iPhone / Pixel | GPS, ambient light, motion | Pi gets location for weather/news/local briefing. |
| iPhone / Pixel | Camera (when foreground)   | Reads a QR / receipt / business card on demand.    |
| Pi             | I²C sensors (DHT22, etc.)  | Phone gets room temperature for context.              |
| Apple Watch / Pixel Watch | Heart rate, motion | Briefing pacing + "are you awake yet?" cue. (M11+)   |

### E. Storage & display

| Offering | Capability                              |
|----------|------------------------------------------|
| Pi       | Bulk storage (USB SSD, NVMe) for podcast cache + Deep Research archives + audiobook library. |
| iPad     | 11"+ display for transcripts, reports, briefing-as-card.                                   |
| Phone    | Cloud storage credentials (iCloud Drive / Google Drive) for opt-in sync.                  |

## 2. Negotiation protocol

Resource sharing rides the **fabric RPC** channel established at
pairing. Topics:

| Topic                       | Direction       | Payload sketch                          |
|-----------------------------|-----------------|------------------------------------------|
| `fabric.capability.advertise` | broadcast     | `{ kind, params, cost_hint }`            |
| `fabric.capability.use`       | client → peer | `{ kind, request_id, args }`             |
| `fabric.capability.result`    | peer → client | `{ request_id, ok, value, error }`       |
| `fabric.capability.cancel`    | client → peer | `{ request_id }`                         |

Examples:

```jsonc
// pi advertises its LLM
{ "topic": "fabric.capability.advertise",
  "data": {
    "kind": "llm.cpu",
    "params": { "model": "qwen2.5-3b-instruct-q4_k_m", "ctx": 4096 },
    "cost_hint": { "tier": "local", "latency_p50_ms": 350, "tok_per_s": 12 }
  }
}

// phone asks pi to answer a question
{ "topic": "fabric.capability.use",
  "data": {
    "kind": "llm.cpu",
    "request_id": "01HZRG…",
    "args": { "prompt": "summarise:\n...", "lang": "pl", "stream": true }
  }
}

// pi streams back tokens
{ "topic": "fabric.capability.result",
  "data": {
    "request_id": "01HZRG…",
    "ok": true,
    "value": { "chunk": "W skrócie…", "final_": false }
  }
}
```

Each peer maintains a **per-capability rate limit + concurrency
limit** so a misbehaving consumer can't saturate the offering
device. Defaults in `configs/fabric.yaml`.

## 3. Selecting the best resource at runtime

When multiple peers offer the same capability, the consumer picks by:

1. **Local first.** If the device can do it itself, it does.
2. **Cost hint comparison.** Lower latency wins, then lower power
   wins (mobile preserves battery), then larger model wins.
3. **User preference override** — the user can pin a capability:
   "Jess, używaj mózgu na Pi" / "use the Pi's brain" → mobile sets
   `llm` preference to `pi.appliance` until rolled back.

## 4. Policy + UI

`configs/fabric.yaml` (synced via the sync log so all nodes agree):

```yaml
version: 1
sharing:
  network:
    iphone_tether_to_pi: { allow: true,  daily_mb_cap: 500 }
    pi_wired_to_phone:   { allow: true }
  compute:
    llm_pi_to_phone:     { allow: true,  loud_confirm: false }
    llm_phone_to_pi:     { allow: true,  loud_confirm: false }
    asr_pi_to_phone:     { allow: false }                   # off by default — phone has good ASR
    asr_phone_to_pi:     { allow: true }
  audio:
    pi_speaker_to_phone: { allow: true }
    phone_buds_to_pi:    { allow: true }
  sensors:
    phone_gps_to_pi:     { allow: true }
    phone_camera_to_pi:  { allow: true,  loud_confirm: true }  # camera = sensitive
  storage:
    pi_storage_to_phone: { allow: true,  per_intent_size_mb: 50 }
```

UI surfaces (mobile):

```
Settings → Fabric → Sharing
   Network                         Compute
   [✓] iPhone tether → Pi          [✓] Pi LLM → phone
   [ ] Pi Ethernet → iPhone        [✓] phone LLM → Pi
   Audio                            Sensors
   [✓] Pi room speaker → phone     [✓] phone GPS → Pi
   [✓] phone buds → Pi             [ ] phone camera → Pi  (loud confirm)
```

Voice equivalents:

| User says                                          | Effect                              |
|----------------------------------------------------|--------------------------------------|
| "Jess, włącz dzielenie mózgu Pi z telefonem"       | `compute.llm_pi_to_phone.allow = true` |
| "Jess, wyłącz aparat z telefonu dla Pi"            | `sensors.phone_camera_to_pi.allow = false` |
| "Jess, używaj zawsze głośnika kuchennego"          | `audio.pi_speaker_to_phone.allow = true` + sets preference. |

## 5. Failure modes

| Failure                                       | What happens                                     |
|-----------------------------------------------|--------------------------------------------------|
| Offering peer goes offline mid-request        | RPC times out; consumer falls back to next-best or local. Announces "Pi nie odpowiada, używam lokalnego mózgu". |
| Daily MB cap reached on phone tether          | Tether stays up but Jessica says "limit danych osiągnięty, oszczędzajmy". |
| Authentication token on the offering peer expires (e.g., for cellular tether) | Re-pair flow triggered next time the capability is asked. |
| Two devices simultaneously offer the same capability | Both get advertised; consumer picks the better-scoring one. |
| User physically takes a node out of range     | After 60 s of unreachability the capability is dropped from the "available" pool; restored on return. |

## 6. Security implications

- All RPCs are end-to-end encrypted by the fabric symmetric key on
  top of TLS — even an attacker who steals the TLS key (e.g., from a
  compromised relay) can't read payloads.
- Sensitive capabilities (camera, microphone-always-on) require
  loud-confirm to enable.
- A device can revoke a single capability at runtime without leaving
  the fabric.
- The fabric does not run a remote-shell capability. Code that wants
  to drive a node must go through the documented RPC vocabulary.

## 7. Examples (PL TL;DR)

**Scenariusz A — gotowanie w kuchni z telefonem w drugim pokoju.**

> Użytkownik: "Hej Jessico, jaka pogoda na jutro?"  
> Pi przejmuje wake (najgłośniejszy mikrofon). Pi nie ma GPS — pyta
> fabric o `sensors.phone_gps_to_pi`. Telefon zwraca lokalizację.
> Pi dzwoni do weather adapter, odpowiada przez głośnik kuchenny.

**Scenariusz B — siedzisz w autobusie ze słuchawkami.**

> Użytkownik: "Jess, deep research o autostradach w Polsce."  
> Telefon przejmuje wake (Pi za daleko). Telefon dzwoni do Gemini
> Deep Research. Po 8 minutach Gemini zwraca raport, telefon
> synchronizuje go do fabric → na Pi pojawia się również, na iPadzie
> również. Telefon czyta podsumowanie przez AirPods. Gdy wrócisz do
> domu, pełny raport otworzysz na iPadzie.

**Scenariusz C — Pi straciło sieć domową.**

> Pi traci IP. Telefon w pokoju widzi `fabric.peer_offline` dla Pi.
> Telefon mówi: "Jess, Pi zgubiło Wi-Fi — mam włączyć hotspot?"  
> Użytkownik: "Tak."  
> Telefon włącza Personal Hotspot z presetowanym SSID `jessica-tether-N`.
> Pi rozpoznaje SSID, łączy się, sync log wraca do działania.
