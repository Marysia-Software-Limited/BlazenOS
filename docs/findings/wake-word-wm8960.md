# Finding: wake-word detection is not viable on the WM8960 HAT mic (2026-06)

**Status:** blocked by mic hardware. Revisit when the Anker S330 (or any quality
USB mic) replaces the ReSpeaker 2-Mics HAT (WM8960). All software pieces below
work; the audio does not carry the wake word with enough fidelity for *any*
detector to discriminate it from background.

## Context

The appliance wants a "Dżesika" wake word so it activates hands-free (alongside
the HAT push-to-talk button). The shipped openWakeWord model (`jessica.onnx`) is
synthetic-trained and scores ~0 on real speech, so it is **not** enabled
(see [`05-MODELS.md`](../05-MODELS.md) and the orchestrator/enable lists). This
investigation tried to build a working detector on the WM8960.

The root problem found earlier in the same hardware bring-up also bites here: the
WM8960 capture is **marginal** — usable by Whisper only with a strong
`initial_prompt` and loud, near-field speech (e.g. "Która godzina" → conf 0.83),
and otherwise unreliable. See the VAD/gain/real-time notes in
[`blazend-audio-in.service`](../../rpi5/stage-blazen/00-install/files/etc/systemd/system/blazend-audio-in.service).

## Methods tried, on real "Dżesika" utterances through the HAT mic

| Method | What it does | Result |
|--------|--------------|--------|
| **Vosk** (`vosk-model-small-pl-0.22`) | streaming Polish ASR / keyword spotting | "Dżesika włącz radio" → `nie będę dąży`; garbage |
| **faster-whisper** (`small`, beam 5, prompt "Dżesika") | robust ASR | clear, standalone "Dżesika ×3" → `Dzień dobry! ×3` |
| **openWakeWord embeddings + cosine** | few-shot: enrol embeddings, match by cosine sim | wake-to-wake 0.995 vs **silence 0.994** — no usable gap |
| **mel-spectrogram + DTW** | speaker-dependent template match on the spectral trajectory | positive 1.64 vs **negative 1.70** — no usable gap |

The two template-matching approaches are the interesting ones: they sidestep
transcription (match the *audio* against an enrolled recording, so the same mic
distortion is present in template and live audio). They are the right technique
and would likely work on a clean mic — but here the signal simply isn't there:
the same word Whisper hears as "Dzień dobry" can't be matched against itself.

## Root cause

Mic hardware. The WM8960 on the ReSpeaker 2-Mics HAT, at the +30 dB gain needed
to clear its noise floor, clips loud speech and otherwise delivers low-fidelity
audio. Every detector — transcription or template — needs the audio to carry the
word; this one does not. Not a software bug; not fixable by tuning.

## Reproduce / revisit (when a better mic is fitted)

The feature extractor is preserved at
[`rpi5/src/blazend/wakeword/features.py`](../../rpi5/src/blazend/wakeword/features.py)
— it runs the on-device openWakeWord ONNX models (`melspectrogram.onnx` →
`embedding_model.onnx`, already baked under `models/wake/`) with only numpy +
onnxruntime. The few-shot recipe that should work on a clean mic:

1. **Enrol**: record the user saying the word ~5× via the ring; keep the
   high-energy windows; store either the embeddings (cosine) or the loudest
   ~0.7 s mel segment (DTW template).
2. **Detect**: slide a word-length window over the live ring, score against the
   template(s) (cosine for embeddings, or DTW distance on the mel sequence), and
   on a confident match touch `/run/blazen/activate` — the same marker the HAT
   button uses, so the existing push-to-talk pipeline (audio-in → ASR → brain →
   TTS) handles the rest unchanged.
3. **Calibrate** the threshold from enrol-vs-background separation. On this HAT
   that separation was ~0 (see the table); on a real mic it should be wide.

The activate-marker path itself is verified working — touching the marker makes
`blazend-audio-in` open a listen window exactly like a button press.

## Recommendation

- **Now:** use the HAT push-to-talk button. The whole voice pipeline runs from a
  button press. (If the button is unresponsive, it is a physical fault — reseat
  the HAT / check the joint; GPIO17 stopped emitting edges mid-session.)
- **Soon:** the Anker S330 (roadmap) — a quality USB mic with AGC — should make
  wake-word detection, Vosk, and accurate ASR all work. Re-run the recipe above.
