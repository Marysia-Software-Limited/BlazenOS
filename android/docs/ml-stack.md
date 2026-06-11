# Android ML stack

Reference for which OS / framework owns which layer of the voice
pipeline. Mirrors the iOS table in `/ios/docs/ml-stack.md` — when one
side moves, the other has to follow or the spec stops being honest.

| Layer        | Tech                                       | Notes |
|--------------|--------------------------------------------|-------|
| Wake word    | openWakeWord ONNX via TFLite (NNAPI / LiteRT) | Two-model parallel loop (PL+EN), like the Pi. ~10-30 ms per 80 ms window on Tensor / Snapdragon. |
| Wake retrain | TFLite on-device personalisation           | Per-user threshold tuning. |
| ASR          | Google Speech-to-Text on-device            | Excellent for PL on Pixel 9 Pro. Lower-end Androids fall back to `whisper.cpp` (small/medium). |
| ASR backup   | `whisper.cpp` via TFLite                   | Shared `small`/`medium` weights with the Pi appliance. |
| VAD          | silero-vad TFLite                          | End-of-utterance detection. |
| Speaker ID   | `mediapipe-tasks-audio` (TFLite)           | 256-d embedding; same shape as iOS. |
| LLM (short)  | Gemini Nano via AICore                     | Pixel 8+, Samsung S24+. Behind a `SDK_INT >= 36` runtime guard. |
| LLM (long)   | Gemini Pro (cloud, opt-in)                 | Same upstream as iOS. |
| TTS          | Android `TextToSpeech` with `pl-PL` voice  | Google Speech Service premium voice download is the default. |
| Vector store | TFLite + SQLite                            | Same shape as iOS. |

## Permissions story

| Permission                            | When requested                  |
|---------------------------------------|---------------------------------|
| `RECORD_AUDIO`                        | First wake-word turn — explained via a pre-prompt overlay |
| `FOREGROUND_SERVICE_MICROPHONE`       | When the user toggles on "always listening" |
| `BLUETOOTH_CONNECT`                   | When routing to AirPods-class accessory     |
| `POST_NOTIFICATIONS`                  | First time a Live Update goes up            |
| `INTERNET`                            | Only on opt-in cloud features (Gemini Pro)  |

## On-device-first contract

This implementation follows the Pi 5 contract verbatim:

1. **No outbound call during normal operation.** Wake / ASR / LLM / TTS
   all run on-device by default. Cloud calls are explicit, per-feature,
   and require an opt-in toggle that's mentioned by name in the briefing.
2. **Telemetry off by default.** Anything that leaves the device flows
   through a single instrumented adapter that the user can disable from
   the Settings tab.
3. **Graceful fallback.** When a layer is missing (e.g. AICore not
   provisioned on the device), the layer above degrades — never crashes.
   See `docs/product/08-PRIVACY-AND-CLOUD.md`.
