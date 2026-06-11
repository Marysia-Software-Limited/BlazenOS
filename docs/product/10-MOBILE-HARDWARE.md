# 10 — Mobile hardware

This doc names **concrete phones / tablets / accessories** to target,
test against, and recommend to users. Two roles:

- **Reference devices** — what we build for, file bugs against,
  measure latency on. Every release must pass on every reference.
- **Supported devices** — we test on a subset, ship to all of these,
  and accept best-effort behaviour on edge cases.

## Reference devices

### iPhone — primary

| Device                  | Year | Chip      | RAM | Neural Engine | Apple Intelligence | Polish ASR | Verdict |
|-------------------------|------|-----------|-----|----------------|---------------------|------------|---------|
| **iPhone 17 Pro**       | 2025 | A19 Pro   | 12 GB | 18-core      | ✓ (full)            | excellent  | **Reference for 2026.** |
| **iPhone 16 Pro**       | 2024 | A18 Pro   | 8 GB  | 16-core      | ✓ (full)            | excellent  | **Reference for the first release.** |
| iPhone 15 Pro / Pro Max | 2023 | A17 Pro   | 8 GB  | 16-core      | ✓ (full)            | excellent  | Supported.   |
| iPhone 15 / 15 Plus     | 2023 | A16       | 6 GB  | 16-core      | ✗ (no Apple Intelligence) | excellent | Supported (cloud LLM only). |
| iPhone 14 Pro / older   | 2022 |           |       |                | ✗                   | good       | Best-effort. |

**Recommended for the primary user (Polish-only, voice-first):**
**iPhone 16 Pro** (256 GB or 512 GB; 256 GB is enough for Jessica +
typical user data). Reasons:

1. Apple Intelligence (on-device 3B LLM) with Polish since iOS 18.4.
2. 16-core Neural Engine = ~3-4 ms per ASR window of `medium` Whisper
   via CoreML; native Speech runs trivially.
3. 8 GB RAM = enough for Apple Intelligence + Jessica app + others.
4. USB-C with DisplayPort alt-mode = a way to debug + project
   briefing on a TV if ever needed.
5. Best-in-class privacy: ATT, on-device intelligence, Secure Enclave.

### iPad — optional companion

| Device              | Year | Chip   | RAM   | Notes                                |
|---------------------|------|--------|-------|--------------------------------------|
| **iPad Pro M4**     | 2024 | M4     | 8–16 GB | Best on-device ML; Apple Intelligence. |
| iPad Pro M2 / Air M2| 2022 | M2     | 8 GB  | Supported.                            |
| iPad mini A17 Pro   | 2024 | A17 Pro| 8 GB  | Compact form factor; supported.       |

Not required, but if the user wants a larger screen for the briefing
or report viewing, **iPad mini A17 Pro** is the cost-effective pick.

### Pixel — Android reference

| Device              | Year | SoC        | RAM | Gemini Nano | Verdict |
|---------------------|------|------------|-----|-------------|---------|
| **Pixel 10 Pro**    | 2025 | Tensor G5  | 16 GB | ✓ (full)  | **Reference for 2026.** |
| **Pixel 9 Pro / XL**| 2024 | Tensor G4  | 16 GB | ✓ (full)  | **Reference for first release.** |
| Pixel 9 / 9a        | 2024 | Tensor G4  | 8 GB | ✓ (limited)| Supported.  |
| Pixel 8 Pro         | 2023 | Tensor G3  | 12 GB | ✓ (full)  | Supported.  |
| Pixel 8 / 8a        | 2023 | Tensor G3  | 8 GB | ✓ (limited)| Supported.  |

For Android: **Pixel 9 Pro**.

### Samsung — Android secondary

| Device              | Year | SoC                         | RAM   | Galaxy AI | Verdict      |
|---------------------|------|------------------------------|-------|-----------|--------------|
| Galaxy S25 Ultra    | 2025 | Snapdragon 8 Elite           | 12 GB | ✓         | Supported.   |
| Galaxy S24 / S24+   | 2024 | Snapdragon 8 Gen 3 / Exynos 2400 | 8 GB | ✓     | Supported.   |
| Galaxy Tab S10 Ultra| 2024 | Dimensity 9300                | 12 GB | ✓         | Supported.   |

Galaxy AI uses Gemini Nano under the hood for the relevant features;
Jessica calls into the same `AICore` API on these devices as on
Pixels.

## Supported devices summary

We **ship to** anything that meets the minimum OS version
([`09-MOBILE-PLATFORM-DECISION.md`](09-MOBILE-PLATFORM-DECISION.md)
§4) but only **guarantee** the experience on reference devices.
Best-effort everywhere else.

## Audio accessories

Quality matters more for voice-first apps than for typical phone apps.

### iPhone-side recommendations

| Accessory                  | Why it's good                                  |
|----------------------------|------------------------------------------------|
| **AirPods Pro 2 (USB-C)**  | Adaptive noise cancellation; tight integration with Personal Voice; in-pocket wake + reply hands-free. |
| AirPods 4 (with ANC)       | Cheaper alternative; same Personal Voice path. |
| AirPods Max (USB-C)        | Best fidelity for podcast/audiobook playback.  |

### Pixel/Android-side recommendations

| Accessory                  | Why it's good                                  |
|----------------------------|------------------------------------------------|
| **Pixel Buds Pro 2**       | Multipoint, Gemini Nano integration, conversational AI.  |
| Sony WF-1000XM5            | Best ANC on Android; supports Multipoint.      |
| Bose QuietComfort Earbuds II | Strong ANC; mature SDK integration.          |

### Universal

| Accessory                          | Why                                       |
|------------------------------------|--------------------------------------------|
| Shokz OpenRun Pro (bone conduction)| Hands-free, leaves ears open — great for walking + briefings. |
| Beyerdynamic DT-770 80Ω (wired)    | Dev rig — no Bluetooth quirks during dev. |

## Wearables (optional, post-M5)

| Device                  | Year | Use case                                       |
|-------------------------|------|------------------------------------------------|
| Apple Watch Series 10   | 2024 | Quick "Jess, ile mam czasu na obiad?" without phone in hand. |
| Apple Watch Ultra 2     | 2023 | Same + outdoor / loud-environment.             |
| Pixel Watch 3 XL        | 2024 | Same on Android.                                |

We provide a companion watch app at M6+ (`rachel/watchOS/` and
`rachel/wearOS/`). At M1-M5, watches are out of scope.

## Hardware app (specific dedicated device?)

The maintainer asked whether a dedicated hardware app makes sense.

We considered three options:

1. **Just the Pi 5 appliance.** Already covered by `blazen_os`.
2. **A purpose-built dedicated handheld** (custom PCB + small screen
   + battery + mic + speaker). Tempting but a massive distraction:
   needs FCC/CE certification, mechanical engineering, supply chain.
   Decline for now.
3. **A repurposed "smart speaker" form factor** that runs `blazen_os`
   (e.g., a Pi 5 inside a printed enclosure with ReSpeaker HAT + a
   small speaker). Yes, this is what we ship at M10 as a "build your
   own Jessica" kit.

> **Decision (2026-06-11):** No dedicated handheld silicon project.
> The two skins (Pi + phone) cover the user base. A printed enclosure
> kit (option 3) ships as M10 reference hardware once the software
> stabilises.

## Reference rig — what the maintainer should buy

For someone in the maintainer's situation (PL-only primary user,
existing Pi 5 16 GB + Hailo on order):

| Item                  | Why                                          |
|-----------------------|----------------------------------------------|
| **iPhone 16 Pro 256 GB** | Primary mobile reference + the primary user's daily driver. |
| **AirPods Pro 2 (USB-C)** | Hands-free voice everywhere. |
| **Pixel 9 Pro 256 GB**| Android reference for cross-platform parity.  |
| **Pixel Buds Pro 2**  | Android-side audio. |
| **Apple Developer account** | Signing + TestFlight. |
| **Google Play Console** | One-time $25.                                |
| **Existing Pi 5 16 GB + 27 W USB-C PSU** | `blazen_os` reference (already on hand). |
| **ReSpeaker 2-Mics Pi HAT** | Already in `02-HARDWARE.md`. |
| **Hailo AI HAT+ 10H (when shipping)** | Optional LLM accelerator (already in `12-ML-ACCELERATOR.md`). |
| **256 GB microSD Pro Endurance** | Reference SD for Pi. |

Total mobile spend: **~3500-4000 PLN** (the phones + buds + dev
accounts) on top of existing Pi hardware. Spread over six months of
development as we hit M2-M6.

## What happens on cheaper phones?

Cheaper iPhones (no Apple Intelligence) and cheaper Androids (no
Gemini Nano) **still work** — they just route more often to cloud
LLM:

```
on-device LLM available?
  ├── yes → on-device for short replies, cloud for long ones (default)
  └── no  → cloud for all LLM, slightly higher latency + bandwidth
            ASR + TTS + wake-word still run on-device.
            Quality of experience drops by ~10-20% but works.
```

We surface the difference in onboarding:

> "Twój telefon nie ma jeszcze lokalnego modelu Apple Intelligence —
> krótsze odpowiedzi pójdą do Google. Możesz zobaczyć ile to kosztuje
> w `Ustawienia → Prywatność → Chmura`."

## PL TL;DR — co kupić

- **iPhone 16 Pro 256 GB** + **AirPods Pro 2** to absolutny minimum
  rekomendowany dla pierwszego polskojęzycznego użytkownika.
- **Pixel 9 Pro 256 GB** + **Pixel Buds Pro 2** dla parytetu po
  stronie Androida (do testów i jako alternatywa, gdyby użytkownik
  wolał Androida).
- **iPad mini A17 Pro** opcjonalnie, jeśli użytkownik chce ekran do
  przeglądania briefingu czy raportów Deep Research.
- **Bez dedykowanego sprzętu** poza Raspberry Pi 5 — własny handheld
  to zbyt duży projekt; zamiast tego w M10 wydamy zestaw
  3D-printable do obudowania Pi.
