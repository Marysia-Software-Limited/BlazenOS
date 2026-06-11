# iOS ML stack

Reference for which Apple framework owns which layer of the voice
pipeline. Mirrors the Android table in `/android/docs/ml-stack.md` —
when one side moves, the other has to follow or the spec stops being
honest.

| Layer        | Tech                                       | Notes |
|--------------|--------------------------------------------|-------|
| Wake word    | openWakeWord ONNX via CoreML conversion    | Runs on the Neural Engine. ~10 ms per 80 ms window on A17/A18. Two-model parallel loop (PL+EN). |
| Wake retrain | CoreML on-device personalisation           | Per-user threshold tuning. |
| ASR          | `Speech` framework, on-device              | iOS 13+ has on-device Polish recognition. iOS 17+ adds streaming + custom vocab. |
| ASR backup   | `whisper.cpp` via CoreML (`small`, `medium`) | Used when offline or for non-supported language; PL on `medium` works well. |
| VAD          | `SoundAnalysis` + a custom VAD model       | End-of-utterance detection. |
| Speaker ID   | `SoundAnalysis` + custom `SNClassifier`    | 256-d embedding; Neural Engine accelerated. |
| LLM (short)  | Apple Intelligence Foundation Models       | iOS 18.4+ on A17 Pro / A18 / A18 Pro. On-device ~3B model. |
| LLM (long)   | Gemini Pro (cloud, opt-in)                 | When the on-device model isn't enough. Same upstream as Android. |
| TTS          | `AVSpeechSynthesizer` with `pl-PL` voice   | Polish Enhanced voice (premium download) for natural prosody. |
| Vector store | CoreML `MLFeatureProvider` + SQLite        | Voice ID + memory embeddings. Same shape as Android. |

## Permissions story

| Permission                       | When requested                          |
|----------------------------------|-----------------------------------------|
| `NSMicrophoneUsageDescription`   | First wake-word turn — pre-prompt overlay before the system dialog |
| `NSSpeechRecognitionUsageDescription` | On first Speech call                |
| Background audio mode            | App boot — already declared in Info.plist |
| Personal Voice                   | Onboarding wizard (iOS 17+ only)        |

## Why iOS is the primary target (for the Polish-first user)

1. **Apple's Speech framework has had Polish on-device since iOS 13** —
   a known-mature, stable API.
2. **Apple Intelligence on iOS 18.4+** gives us an on-device LLM
   without a separate licensing path.
3. **Personal Voice / Live Speech (iOS 17+)** is a first-class per-user
   voice learning capability already in the OS.
4. **Privacy story** is the cleanest — Mail and Calendar APIs are
   native, Keychain is mature.
5. **AirPods routing** is trivial with `AVAudioSession`.
6. **TTS Polish voices** on iOS are some of the best on any consumer
   platform.

See [`/docs/product/09-MOBILE-PLATFORM-DECISION.md`](../../docs/product/09-MOBILE-PLATFORM-DECISION.md)
for the full rationale.

## On-device-first contract

Same as Android — see `/android/docs/ml-stack.md`. Cloud calls are
opt-in, explicit per-feature, and named in the briefing.
